create table if not exists public.faqs (
  id bigint primary key,
  category text not null,
  q text not null,
  a text not null,
  keywords text[] not null default '{}',
  image_url text
);

alter table public.faqs add column if not exists image_url text;

create table if not exists public.app_settings (
  id integer primary key check (id = 1),
  settings jsonb not null
);

alter table public.faqs enable row level security;
alter table public.app_settings enable row level security;

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'helpdesk-images',
  'helpdesk-images',
  true,
  5242880,
  array['image/jpeg', 'image/png', 'image/webp']
)
on conflict (id) do update
set public = excluded.public,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'storage'
      and tablename = 'objects'
      and policyname = 'Public read helpdesk images'
  ) then
    create policy "Public read helpdesk images"
      on storage.objects for select
      using (bucket_id = 'helpdesk-images');
  end if;
end
$$;
