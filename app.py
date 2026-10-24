"""FAQ chatbot for The Kavery Engineering College."""

import json
import hmac
import os
import re
import secrets
import uuid
from difflib import SequenceMatcher
from functools import wraps
from pathlib import Path

from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.datastructures import FileStorage
from werkzeug.security import check_password_hash, generate_password_hash


FAQ_PATH = Path(__file__).with_name("kavery_faq.json")
SETTINGS_PATH = Path(__file__).with_name("site_settings.json")
CREDENTIALS_PATH = Path(__file__).with_name("admin_credentials.json")
load_dotenv(Path(__file__).with_name(".env"))
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "").lower()
    in {"1", "true", "yes"},
)
FALLBACK_ANSWER = (
    "Your query is not currently covered in my available knowledge base. Please provide additional details so I can assist you more effectively."
)
DEFAULT_SETTINGS = {
    "college_name": "The Kavery Engineering College (Autonomous)",
    "location": "Mecheri, Salem",
    "college_code": "2625",
    "helpdesk_title": "TKEC AI Helpdesk",
    "welcome_title": "Vanakkam! How can I help?",
    "welcome_text": "Ask a question or choose one from the FAQ sidebar. Answers are from TKEC's college information.",
    "welcome_message": "Welcome to TKEC AI Helpdesk! College, fees, courses, hostel, bus routes, placements — edha vena kelunga.",
    "composer_hint": "Answers come from the college FAQ database. For unlisted details, contact the college.",
    "input_placeholder": "Message TKEC Helpdesk...",
    "fallback_answer": FALLBACK_ANSWER,
    "contact_phone": "04298-279158",
    "contact_website": "https://tkec.edu.in",
    "quick_topics": "Fees, Hostel, Placement, Bus routes, Contact, Admission",
}
STOP_WORDS = {"a", "an", "are", "can", "do", "does", "for", "how", "i", "is", "it",
              "me", "of", "the", "to", "what", "when", "where", "which", "who"}
_supabase_client = None
_supabase_client_config = None
IMAGE_BUCKET = "helpdesk-images"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def get_supabase_client():
    """Return the configured Supabase client, or None for local JSON mode."""
    global _supabase_client, _supabase_client_config
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_KEY", "").strip()
    if not url and not key:
        return None
    if not url or not key:
        raise RuntimeError("Set both SUPABASE_URL and SUPABASE_KEY in .env.")
    config = (url, key)
    if _supabase_client is None or _supabase_client_config != config:
        from supabase import create_client

        _supabase_client = create_client(url, key)
        _supabase_client_config = config
    return _supabase_client


def validate_faqs(data):
    """Validate FAQ records before using or storing them."""
    if not isinstance(data, list):
        raise ValueError("FAQ data must contain a list of FAQs.")
    seen_ids = set()
    for item in data:
        if not isinstance(item, dict) or not all(
            key in item for key in ("id", "category", "q", "a", "keywords")
        ):
            raise ValueError("Each FAQ must include id, category, q, a, and keywords.")
        if (
            not isinstance(item["id"], int)
            or item["id"] in seen_ids
            or not all(isinstance(item[key], str) for key in ("category", "q", "a"))
            or not isinstance(item["keywords"], list)
            or not all(isinstance(keyword, str) for keyword in item["keywords"])
        ):
            raise ValueError("FAQ IDs, text fields, or keywords are invalid.")
        image_url = item.setdefault("image_url", None)
        if image_url is not None and not isinstance(image_url, str):
            raise ValueError("FAQ image_url must be a string or null.")
        seen_ids.add(item["id"])
    return data


def load_faqs():
    """Load FAQ data and validate the fields used by the matcher and admin."""
    client = get_supabase_client()
    if client:
        result = client.table("faqs").select("*").order("id").execute()
        if not result.data:
            seed_data = load_faqs_from_file()
            for start in range(0, len(seed_data), 500):
                client.table("faqs").insert(seed_data[start:start + 500]).execute()
            result = client.table("faqs").select("*").order("id").execute()
        return validate_faqs(result.data)
    return load_faqs_from_file()


def load_faqs_from_file():
    with FAQ_PATH.open(encoding="utf-8") as faq_file:
        data = json.load(faq_file)
    return validate_faqs(data)


def save_faq(faq):
    client = get_supabase_client()
    if client:
        client.table("faqs").upsert(faq).execute()
        return
    faqs = load_faqs_from_file()
    existing = next((item for item in faqs if item["id"] == faq["id"]), None)
    if existing:
        existing.update(faq)
    else:
        faqs.append(faq)
    save_json(FAQ_PATH, faqs)


def delete_faq(faq_id):
    client = get_supabase_client()
    if client:
        client.table("faqs").delete().eq("id", faq_id).execute()
        return
    faqs = load_faqs_from_file()
    save_json(FAQ_PATH, [faq for faq in faqs if faq["id"] != faq_id])


def inspect_image_upload(upload):
    if not upload or not upload.filename:
        return None
    content = upload.stream.read(MAX_IMAGE_BYTES + 1)
    upload.stream.seek(0)
    if len(content) > MAX_IMAGE_BYTES:
        raise ValueError("Images must be 5 MB or smaller.")
    if content.startswith(b"\xff\xd8\xff"):
        content_type = "image/jpeg"
    elif content.startswith(b"\x89PNG\r\n\x1a\n"):
        content_type = "image/png"
    elif (
        len(content) >= 12
        and content[:4] == b"RIFF"
        and content[8:12] == b"WEBP"
    ):
        content_type = "image/webp"
    else:
        raise ValueError("Upload a valid JPG, PNG, or WebP image.")
    return content, content_type


def store_faq_image(upload, faq_id):
    if not upload or not upload.filename:
        return None
    content, content_type = inspect_image_upload(upload)
    object_path = f"faq/{faq_id}/{uuid.uuid4().hex}{IMAGE_TYPES[content_type]}"
    client = get_supabase_client()
    if client:
        client.storage.from_(IMAGE_BUCKET).upload(
            object_path,
            content,
            {"content-type": content_type, "upsert": "true"},
        )
        return client.storage.from_(IMAGE_BUCKET).get_public_url(object_path)

    upload_folder = Path(app.static_folder) / "uploads"
    upload_folder.mkdir(parents=True, exist_ok=True)
    image_path = upload_folder / object_path.replace("/", "_")
    image_path.write_bytes(content)
    return url_for("static", filename=f"uploads/{image_path.name}")


def delete_faq_image(image_url):
    if not image_url:
        return
    client = get_supabase_client()
    if client:
        marker = f"/storage/v1/object/public/{IMAGE_BUCKET}/"
        if marker not in image_url:
            return
        object_path = image_url.split(marker, 1)[1]
        client.storage.from_(IMAGE_BUCKET).remove([object_path])
        return
    prefix = url_for("static", filename="uploads/")
    if not image_url.startswith(prefix):
        return
    image_name = image_url[len(prefix):]
    image_path = (Path(app.static_folder) / "uploads" / image_name).resolve()
    upload_folder = (Path(app.static_folder) / "uploads").resolve()
    if image_path.parent == upload_folder and image_path.is_file():
        image_path.unlink()


def save_json(path, data):
    """Write JSON atomically so an interrupted update cannot truncate the data."""
    temporary_path = path.with_name(f".{path.name}.tmp")
    with temporary_path.open("w", encoding="utf-8") as data_file:
        json.dump(data, data_file, ensure_ascii=False, indent=2)
        data_file.write("\n")
    temporary_path.replace(path)


def load_settings():
    client = get_supabase_client()
    if client:
        result = (
            client.table("app_settings")
            .select("settings")
            .eq("id", 1)
            .limit(1)
            .execute()
        )
        if result.data:
            settings = result.data[0]["settings"]
        elif SETTINGS_PATH.exists():
            with SETTINGS_PATH.open(encoding="utf-8") as settings_file:
                settings = json.load(settings_file)
            save_settings(settings)
        else:
            settings = DEFAULT_SETTINGS.copy()
            save_settings(settings)
        if not isinstance(settings, dict):
            raise ValueError("Supabase app_settings.settings must be a JSON object.")
        return {**DEFAULT_SETTINGS, **settings}
    if not SETTINGS_PATH.exists():
        return DEFAULT_SETTINGS.copy()
    with SETTINGS_PATH.open(encoding="utf-8") as settings_file:
        settings = json.load(settings_file)
    if not isinstance(settings, dict):
        raise ValueError("site_settings.json must contain a JSON object.")
    return {**DEFAULT_SETTINGS, **settings}


def save_settings(settings):
    client = get_supabase_client()
    if client:
        client.table("app_settings").upsert(
            {"id": 1, "settings": settings}
        ).execute()
        return
    save_json(SETTINGS_PATH, settings)


def load_admin_credentials():
    if CREDENTIALS_PATH.exists():
        with CREDENTIALS_PATH.open(encoding="utf-8") as credentials_file:
            credentials = json.load(credentials_file)
        if (
            not isinstance(credentials, dict)
            or not isinstance(credentials.get("username"), str)
            or not isinstance(credentials.get("password_hash"), str)
        ):
            raise ValueError("admin_credentials.json contains invalid credentials.")
        return credentials

    initial_password = os.environ.get("TKEC_ADMIN_PASSWORD")
    if not initial_password:
        return None
    credentials = {
        "username": os.environ.get("TKEC_ADMIN_USERNAME", "admin"),
        "password_hash": generate_password_hash(initial_password),
    }
    save_json(CREDENTIALS_PATH, credentials)
    return credentials


def contact_details(settings):
    phone = settings["contact_phone"]
    tel_number = re.sub(r"[^\d+]", "", phone)
    return {
        "phone": phone,
        "website": settings["contact_website"],
        "call_link": f"tel:{tel_number}",
    }


def csrf_token():
    token = session.get("csrf_token")
    if token is None:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def require_csrf():
    supplied_token = request.form.get("csrf_token", "")
    if not hmac.compare_digest(supplied_token, session.get("csrf_token", "")):
        abort(400, description="Invalid or missing form security token.")


def admin_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)

    return wrapped_view


def normalize(text):
    """Normalize punctuation and whitespace for consistent matching."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s&+@.-]", " ", text.lower())).strip()


def matching_text(text):
    return " ".join(word for word in normalize(text).split() if word not in STOP_WORDS)


def find_best_answer(user_query, faqs=None, settings=None):
    """Return the closest FAQ match, or a contact-oriented fallback."""
    faqs = load_faqs() if faqs is None else faqs
    settings = load_settings() if settings is None else settings
    fallback_answer = settings["fallback_answer"]
    contacts = contact_details(settings)
    query = normalize(user_query)
    if not query:
        return {
            "text": fallback_answer,
            "type": "contact",
            "category": "Contact",
            "score": 0.0,
            "contact": contacts,
        }

    query_text = matching_text(query)
    if not query_text:
        return {
            "text": fallback_answer,
            "type": "contact",
            "category": "Contact",
            "score": 0.0,
            "contact": contacts,
        }

    best_faq = None
    best_score = 0.0
    best_question_score = -1.0
    best_specificity = -1
    query_words = set(query_text.split())
    category_query = query if query in {normalize(faq["category"]) for faq in faqs} else None

    for faq in faqs:
        if category_query and normalize(faq["category"]) != category_query:
            continue
        question_score = SequenceMatcher(
            None, query_text, matching_text(faq["q"])
        ).ratio()
        keyword_matches = [
            keyword for keyword in faq["keywords"]
            if set(normalize(keyword).split()) & query_words
        ]
        keyword_score = max(
            (
                SequenceMatcher(None, query_text, matching_text(keyword)).ratio()
                for keyword in faq["keywords"]
                if matching_text(keyword)
            ),
            default=0.0,
        )
        faq_words = set(matching_text(faq["q"]).split())
        for keyword in faq["keywords"]:
            faq_words.update(matching_text(keyword).split())
        if not query_words & faq_words:
            continue

        score = min(
            1.0,
            max(question_score, keyword_score) + (0.2 if keyword_matches else 0.0),
        )
        specificity = int(normalize(faq["category"]) != "department")
        if (
            score > best_score
            or (
                score == best_score
                and question_score > best_question_score
            )
            or (
                score == best_score
                and question_score == best_question_score
                and specificity > best_specificity
            )
        ):
            best_faq = faq
            best_score = score
            best_question_score = question_score
            best_specificity = specificity

    if best_faq is not None and best_score > 0.4:
        return {
            "text": best_faq["a"],
            "type": "faq",
            "category": best_faq["category"],
            "score": best_score,
            "contact": None,
            "image_url": best_faq.get("image_url"),
        }
    return {
        "text": fallback_answer,
        "type": "contact",
        "category": "Contact",
        "score": best_score,
        "contact": contacts,
        "image_url": None,
    }


@app.get("/")
def index():
    settings = load_settings()
    settings["quick_topics"] = [
        topic.strip() for topic in settings["quick_topics"].split(",") if topic.strip()
    ]
    return render_template("index.html", faqs=load_faqs(), settings=settings)


@app.get("/chat")
def chat():
    result = find_best_answer(request.args.get("q", ""))
    return jsonify(
        answer=result["text"],
        type=result["type"],
        category=result["category"],
        score=round(result["score"], 2),
        matched=result["type"] == "faq",
        contact=result["contact"],
        image_url=result.get("image_url"),
    )


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if session.get("is_admin"):
        return redirect(url_for("admin_dashboard"))
    error = None
    credentials = load_admin_credentials()
    password_configured = credentials is not None
    if request.method == "POST":
        require_csrf()
        credentials = load_admin_credentials()
        if credentials is None:
            error = "Admin login is disabled until TKEC_ADMIN_PASSWORD is configured."
        elif (
            hmac.compare_digest(
                request.form.get("username", ""), credentials["username"]
            )
            and check_password_hash(
                credentials["password_hash"], request.form.get("password", "")
            )
        ):
            session.clear()
            session["is_admin"] = True
            csrf_token()
            return redirect(url_for("admin_dashboard"))
        else:
            error = "Incorrect password."
    return render_template(
        "admin_login.html",
        error=error,
        password_configured=password_configured,
        csrf_token=csrf_token(),
    )


@app.post("/admin/password")
@admin_required
def admin_change_password():
    require_csrf()
    credentials = load_admin_credentials()
    if credentials is None:
        abort(503, description="Admin credentials are not configured.")
    current_password = request.form.get("current_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")
    if not check_password_hash(credentials["password_hash"], current_password):
        flash("Current password is incorrect.", "error")
    elif len(new_password) < 12:
        flash("New password must be at least 12 characters.", "error")
    elif not hmac.compare_digest(new_password, confirm_password):
        flash("New password and confirmation do not match.", "error")
    else:
        credentials["password_hash"] = generate_password_hash(new_password)
        save_json(CREDENTIALS_PATH, credentials)
        flash("Admin password changed. Use the new password next time you sign in.", "success")
    return redirect(url_for("admin_dashboard", _anchor="password"))


@app.post("/admin/logout")
@admin_required
def admin_logout():
    require_csrf()
    session.clear()
    return redirect(url_for("admin_login"))


@app.get("/admin")
@admin_required
def admin_dashboard():
    faqs = load_faqs()
    query = request.args.get("q", "").strip().lower()
    if query:
        faqs = [
            faq for faq in faqs
            if query in faq["q"].lower()
            or query in faq["category"].lower()
            or query in faq["a"].lower()
            or any(query in keyword.lower() for keyword in faq["keywords"])
        ]
    page = max(request.args.get("page", 1, type=int), 1)
    per_page = 40
    total_pages = max((len(faqs) + per_page - 1) // per_page, 1)
    page = min(page, total_pages)
    return render_template(
        "admin.html",
        faqs=faqs[(page - 1) * per_page:page * per_page],
        settings=load_settings(),
        query=query,
        page=page,
        total_pages=total_pages,
        total_faqs=len(load_faqs()),
        csrf_token=csrf_token(),
    )


@app.route("/admin/faq/new", methods=["GET", "POST"])
@admin_required
def admin_new_faq():
    form_values = None
    if request.method == "POST":
        require_csrf()
        category = request.form.get("category", "").strip()
        question = request.form.get("question", "").strip()
        answer = request.form.get("answer", "").strip()
        keywords = parse_keywords(request.form.get("keywords", ""))
        error = validate_faq_fields(category, question, answer, keywords)
        try:
            image_upload = inspect_image_upload(request.files.get("image"))
        except ValueError as upload_error:
            error = str(upload_error)
            image_upload = None
        if error:
            flash(error, "error")
            form_values = {
                "category": category,
                "q": question,
                "a": answer,
                "keywords": keywords,
            }
        else:
            faqs = load_faqs()
            faq_id = max((faq["id"] for faq in faqs), default=0) + 1
            faq = {
                "id": faq_id,
                "category": category,
                "q": question,
                "a": answer,
                "keywords": keywords,
                "image_url": None,
            }
            if image_upload:
                faq["image_url"] = store_faq_image(request.files.get("image"), faq_id)
            save_faq(faq)
            flash("FAQ added.", "success")
            return redirect(url_for("admin_dashboard"))
    return render_template(
        "admin_faq_form.html",
        faq=None,
        form_values=form_values,
        csrf_token=csrf_token(),
    )


@app.route("/admin/faq/<int:faq_id>/edit", methods=["GET", "POST"])
@admin_required
def admin_edit_faq(faq_id):
    faqs = load_faqs()
    faq = next((entry for entry in faqs if entry["id"] == faq_id), None)
    if faq is None:
        abort(404)
    form_values = None
    if request.method == "POST":
        require_csrf()
        category = request.form.get("category", "").strip()
        question = request.form.get("question", "").strip()
        answer = request.form.get("answer", "").strip()
        keywords = parse_keywords(request.form.get("keywords", ""))
        error = validate_faq_fields(category, question, answer, keywords)
        try:
            image_upload = inspect_image_upload(request.files.get("image"))
        except ValueError as upload_error:
            error = str(upload_error)
            image_upload = None
        if error:
            flash(error, "error")
            form_values = {
                "category": category,
                "q": question,
                "a": answer,
                "keywords": keywords,
            }
        else:
            previous_image = faq.get("image_url")
            if image_upload:
                faq["image_url"] = store_faq_image(
                    request.files.get("image"), faq_id
                )
            elif request.form.get("remove_image") == "on":
                faq["image_url"] = None
            faq.update(category=category, q=question, a=answer, keywords=keywords)
            save_faq(faq)
            if previous_image and faq.get("image_url") != previous_image:
                delete_faq_image(previous_image)
            flash("FAQ updated.", "success")
            return redirect(url_for("admin_dashboard"))
    return render_template(
        "admin_faq_form.html",
        faq=faq,
        form_values=form_values,
        csrf_token=csrf_token(),
    )


@app.post("/admin/faq/<int:faq_id>/delete")
@admin_required
def admin_delete_faq(faq_id):
    require_csrf()
    faqs = load_faqs()
    deleted_faq = next((faq for faq in faqs if faq["id"] == faq_id), None)
    updated_faqs = [faq for faq in faqs if faq["id"] != faq_id]
    if len(updated_faqs) == len(faqs):
        abort(404)
    delete_faq(faq_id)
    if deleted_faq:
        delete_faq_image(deleted_faq.get("image_url"))
    flash("FAQ deleted.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/settings")
@admin_required
def admin_save_settings():
    require_csrf()
    fields = tuple(DEFAULT_SETTINGS)
    settings = {field: request.form.get(field, "").strip() for field in fields}
    missing = [field.replace("_", " ") for field, value in settings.items() if not value]
    if missing:
        flash(f"Please fill in all settings. Missing: {', '.join(missing)}.", "error")
    elif not settings["contact_website"].startswith(("https://", "http://")):
        flash("Contact website must begin with http:// or https://.", "error")
    else:
        save_settings(settings)
        flash("Website settings saved.", "success")
    return redirect(url_for("admin_dashboard", _anchor="settings"))


def parse_keywords(value):
    return [keyword.strip() for keyword in re.split(r"[,\n]", value) if keyword.strip()]


def validate_faq_fields(category, question, answer, keywords):
    if not category or not question or not answer:
        return "Category, question, and answer are required."
    if not keywords:
        return "Add at least one keyword."
    return None


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
