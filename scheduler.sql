-- Independent scheduler. Only operational timestamps/statuses, no listener data.
create extension if not exists pg_cron;
create extension if not exists pg_net with schema extensions;
create schema if not exists guide_ops;
revoke all on schema guide_ops from public, anon, authenticated;
alter default privileges in schema guide_ops revoke execute on functions from public;

create table if not exists guide_ops.refresh_state (
  id boolean primary key default true check(id),
  enabled boolean not null default false,
  last_tick timestamptz,
  last_poll timestamptz,
  poll_request bigint,
  last_publication timestamptz,
  publication_checked_at timestamptz,
  last_dispatch timestamptz,
  dispatch_request bigint,
  last_dispatch_status integer,
  last_error text
);
alter table guide_ops.refresh_state enable row level security;
insert into guide_ops.refresh_state(id) values(true) on conflict do nothing;
create table if not exists guide_ops.refresh_events (
  id bigint generated always as identity primary key,
  happened_at timestamptz not null default now(),
  event text not null,
  detail text
);
alter table guide_ops.refresh_events enable row level security;
revoke all on all tables in schema guide_ops from public,anon,authenticated;

create or replace function guide_ops.tick() returns void
language plpgsql security invoker set search_path = '' as $$
declare
  s guide_ops.refresh_state%rowtype;
  r record;
  manifest jsonb;
  stamp timestamptz;
  secret_value text;
  request_id bigint;
begin
  -- Only one tick can queue work at a time, including manual verification.
  if not pg_try_advisory_xact_lock(817045100) then return; end if;
  select * into s from guide_ops.refresh_state where id for update;
  update guide_ops.refresh_state set last_tick=now() where id;
  delete from guide_ops.refresh_events where happened_at<now()-interval '7 days';
  delete from cron.job_run_details where jobid in (select jobid from cron.job where jobname='uk-tv-preview-refresh') and start_time<now()-interval '7 days';

  if s.poll_request is not null then
    select status_code,content,timed_out,error_msg into r from net._http_response where id=s.poll_request;
    if found then
      if r.status_code=200 and not coalesce(r.timed_out,false) and r.error_msg is null then
        begin
          manifest=r.content::jsonb;
          stamp=to_timestamp((manifest->>'checked_at')::double precision);
          if manifest->>'schema' is distinct from '1' or stamp is null or stamp>now()+interval '5 minutes'
             or coalesce(manifest->>'sha256','') !~ '^[a-f0-9]{64}$'
             or coalesce(manifest->>'file','') <> 'guide-'||coalesce(manifest->>'sha256','')||'.json.gz'
             or coalesce((manifest->>'programmes')::integer,0)<1000
          then raise exception 'Invalid manifest'; end if;
          if s.last_publication is not null and stamp<s.last_publication then raise exception 'Manifest rollback'; end if;
          if s.last_publication is distinct from stamp then
            insert into guide_ops.refresh_events(event,detail) values('publication_observed',stamp::text);
          end if;
          update guide_ops.refresh_state set last_publication=stamp,publication_checked_at=now(),last_error=null where id;
          s.last_publication=stamp;s.publication_checked_at=now();
        exception when others then
          update guide_ops.refresh_state set last_error='Manifest validation failed' where id;
          insert into guide_ops.refresh_events(event) values('manifest_invalid');
        end;
      else
        update guide_ops.refresh_state set last_error='Manifest check failed' where id;
        insert into guide_ops.refresh_events(event,detail) values('manifest_failed',coalesce(r.status_code::text,'network'));
      end if;
      update guide_ops.refresh_state set poll_request=null where id;
      s.poll_request=null;
    elsif s.last_poll<now()-interval '2 minutes' then
      update guide_ops.refresh_state set poll_request=null,last_error='Manifest request timed out' where id;
      s.poll_request=null;
    end if;
  end if;

  if s.dispatch_request is not null then
    select status_code,timed_out,error_msg into r from net._http_response where id=s.dispatch_request;
    if found then
      update guide_ops.refresh_state set dispatch_request=null,last_dispatch_status=r.status_code,
        last_error=case when r.status_code in (200,204) then null else 'Refresh dispatch failed' end where id;
      insert into guide_ops.refresh_events(event,detail) values(
        case when r.status_code in (200,204) then 'dispatch_accepted' else 'dispatch_failed' end,
        coalesce(r.status_code::text,'network'));
      s.dispatch_request=null;
    elsif s.last_dispatch<now()-interval '2 minutes' then
      update guide_ops.refresh_state set dispatch_request=null,last_error='Dispatch request timed out' where id;
      s.dispatch_request=null;
    end if;
  end if;

  -- Manifest checks every two minutes. Refresh due 30 minutes after collection
  -- began; a maximum one dispatch per 25 minutes bounds recovery attempts.
  if s.poll_request is null and (s.last_poll is null or s.last_poll<=now()-interval '2 minutes') then
    request_id=net.http_get(
      url:='https://sidneytambin-maker.github.io/uk-tv-listings-preview-data/current.json',
      headers:=jsonb_build_object('User-Agent','UKTVListingsScheduler/1.0','Cache-Control','no-cache'),
      timeout_milliseconds:=15000);
    update guide_ops.refresh_state set poll_request=request_id,last_poll=now() where id;
  end if;
  if s.enabled and s.dispatch_request is null
     and (s.last_publication is null or s.last_publication<=now()-interval '30 minutes')
     and (s.last_dispatch is null or s.last_dispatch<=now()-interval '25 minutes') then
    select decrypted_secret into secret_value from vault.decrypted_secrets where name='uk_tv_preview_refresh_github' limit 1;
    if secret_value is null or length(secret_value)<20 then
      update guide_ops.refresh_state set last_error='Refresh connection not configured' where id;
      return;
    end if;
    request_id=net.http_post(
      url:='https://api.github.com/repos/sidneytambin-maker/uk-tv-listings-preview-data/actions/workflows/refresh.yml/dispatches',
      headers:=jsonb_build_object('User-Agent','UKTVListingsScheduler/1.0','Accept','application/vnd.github+json',
        'X-GitHub-Api-Version','2026-03-10','Authorization','Bearer '||secret_value),
      body:=jsonb_build_object('ref','main'),timeout_milliseconds:=15000);
    update guide_ops.refresh_state set dispatch_request=request_id,last_dispatch=now(),last_dispatch_status=null where id;
    insert into guide_ops.refresh_events(event) values('dispatch_queued');
  end if;
end $$;
revoke all on function guide_ops.tick() from public,anon,authenticated;
select cron.schedule('uk-tv-preview-refresh','* * * * *','select guide_ops.tick();');
