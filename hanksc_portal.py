"""Optional HANK SC calendar/archive. Legacy readiness is intentionally independent.
All persistent metadata is in a new private worksheet; binaries in private Storage.
No client browser receives server credentials or an unrestricted object URL.
"""
from __future__ import annotations
import calendar
import hashlib
import io
import json
import re
import uuid
import zipfile
from datetime import date, datetime, time, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo
from urllib.parse import urlencode, quote

import requests
import streamlit as st
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials as UserCredentials

TZ = ZoneInfo('Europe/Bratislava')
SHEET = 'portal_events_v1'
COLUMNS = ['revision_id', 'event_id', 'updated_at', 'actor', 'members_json', 'title',
           'start', 'end', 'location', 'status', 'training_notes', 'private_notes',
           'files_json', 'invite_status', 'invite_emails_json']
MAX_BYTES = 10 * 1024 * 1024


def norm(value):
    return ' '.join(str(value).strip().split()).casefold()


def accounts():
    return dict(st.secrets.get('PORTAL_ACCOUNTS', {}))


def roster():
    names = set(str(n) for n in dict(st.secrets.get('CLIENT_CODES', {})))
    for a in accounts().values():
        names.update(str(n) for n in a.get('athletes', []))
    return sorted(names, key=norm)


def google_login_button():
    if not st.secrets.get('PORTAL_GOOGLE_LOGIN', False):
        return
    if not hasattr(st, 'login'):
        st.warning('Google login requires an updated Streamlit installation.')
        return
    if st.button('Prihlásiť cez Google', use_container_width=True):
        st.login()


def restore_google_identity():
    if not st.secrets.get('PORTAL_GOOGLE_LOGIN', False):
        return
    user = getattr(st, 'user', None)
    if user is None or not user.is_logged_in:
        # A legacy login may coexist with enabled Google login.
        if st.session_state.get('portal_google'):
            for k in ['hank_role', 'hank_athlete', 'portal_members', 'portal_google', 'portal_actor']:
                st.session_state.pop(k, None)
        return
    email = str(user.get('email', '')).lower().strip()
    entry = {str(k).lower(): v for k, v in accounts().items()}.get(email)
    if user.get('email_verified') is not True or not entry:
        st.error('Tento overený Google účet nemá pridelený prístup. Kontaktuj trénera.')
        if st.button('Odhlásiť Google účet'):
            st.logout()
        st.stop()
    role = entry.get('role', 'athlete')
    if role not in ('coach', 'athlete', 'parent'):
        st.error('Neplatná konfigurácia účtu.'); st.stop()
    members = list(entry.get('athletes', []))
    if role != 'coach' and not members:
        st.error('Účet nemá priradeného športovca.'); st.stop()
    # Never silently switch profiles while a legacy check-in is pending.
    if st.session_state.get('hank_pending') and st.session_state.get('portal_actor') != email:
        st.warning('Najprv dokonči alebo obnov pôvodné prihlásenie a ulož rozpracovaný check-in.')
        if st.button('Odhlásiť Google účet'):
            st.logout()
        st.stop()
    st.session_state.update(hank_role='coach' if role == 'coach' else 'athlete',
                            portal_actor=email, portal_members=members, portal_google=True)
    if role != 'coach' and st.session_state.get('hank_athlete') not in members:
        st.session_state['hank_athlete'] = members[0]


def account_switcher():
    members = st.session_state.get('portal_members', []) if st.session_state.get('portal_google') else []
    if st.session_state.get('hank_role') == 'athlete' and len(members) > 1:
        active = st.session_state.get('hank_athlete', members[0])
        selected = st.sidebar.selectbox('Profil športovca', members, index=members.index(active),
                                       disabled=bool(st.session_state.get('hank_pending')))
        if active != selected:
            # Clear profile-specific form state so a sibling's answers are not reused.
            keep = {k: v for k, v in st.session_state.items() if k in
                    ('hank_role', 'portal_actor', 'portal_members', 'portal_google')}
            st.session_state.clear(); st.session_state.update(keep, hank_athlete=selected)
            st.rerun()


def actor():
    return str(st.session_state.get('portal_actor') or
               ('coach' if is_coach() else 'legacy:' + norm(st.session_state.get('hank_athlete', ''))))


def is_coach():
    return st.session_state.get('hank_role') == 'coach'


def member_scope():
    # Calendar follows the selected profile. Google parents can switch profiles above.
    a = st.session_state.get('hank_athlete', '')
    return [a] if st.session_state.get('hank_role') == 'athlete' and a else []


def can_read(event, scope, coach=False):
    return coach or bool({norm(x) for x in scope} & {norm(x) for x in event['members']})


def can_edit(event, scope, who, coach=False):
    return coach or (event['status'] == 'Návrh' and event['actor'] == who and
                     set(map(norm, event['members'])).issubset(set(map(norm, scope))))


def visible_events(events, scope, coach=False):
    # Drop private fields before handing records to any non-coach UI renderer.
    out = []
    for e in events:
        if can_read(e, scope, coach):
            item = dict(e)
            if not coach:
                item.pop('private_notes', None)
                item.pop('invite_emails', None)
            out.append(item)
    return out


def validate_event(event):
    if not event.get('members') or not event.get('title', '').strip():
        raise ValueError('Vyber športovca a vyplň názov.')
    start, end = datetime.fromisoformat(event['start']), datetime.fromisoformat(event['end'])
    if start.tzinfo is None or end.tzinfo is None or not timedelta(0) < end - start <= timedelta(hours=8):
        raise ValueError('Neplatný čas alebo dĺžka tréningu (1 až 480 minút).')
    if event['status'] not in ('Návrh', 'Potvrdené', 'Absolvované', 'Zrušené'):
        raise ValueError('Neplatný stav.')
    for key in ('title', 'location', 'training_notes', 'private_notes'):
        if len(event.get(key, '')) > (10000 if 'notes' in key else 200):
            raise ValueError('Text je príliš dlhý.')


def local_datetime(day, clock):
    naive = datetime.combine(day, clock)
    local = naive.replace(tzinfo=TZ)
    # Fail rather than silently change a non-existent or ambiguous DST time.
    if local.astimezone(timezone.utc).astimezone(TZ).replace(tzinfo=None) != naive:
        raise ValueError('Tento čas pri zmene letného času neexistuje.')
    if naive.replace(tzinfo=TZ, fold=0).utcoffset() != naive.replace(tzinfo=TZ, fold=1).utcoffset():
        raise ValueError('Tento čas je pri zmene letného času nejednoznačný. Vyber iný čas.')
    return local


class Store:
    def __init__(self, client):
        self.book = client.open_by_key(st.secrets['GOOGLE_SHEET_ID'])

    def worksheet(self):
        import gspread
        try:
            ws = self.book.worksheet(SHEET)
        except gspread.WorksheetNotFound:
            raise ValueError('Kalendár ešte nie je inicializovaný. Tréner ho nastaví v tejto sekcii.')
        if ws.row_values(1) != COLUMNS:
            raise ValueError('Hlavičky portálu sa nezhodujú. Dáta sa nezmenili.')
        return ws

    def initialize(self):
        if not is_coach():
            raise PermissionError('Len tréner môže inicializovať portál.')
        import gspread
        try:
            self.book.worksheet(SHEET)
        except gspread.WorksheetNotFound:
            ws = self.book.add_worksheet(SHEET, rows=1000, cols=len(COLUMNS))
            ws.append_row(COLUMNS, value_input_option='RAW')
        self.worksheet()

    def all(self):
        values = self.worksheet().get_all_values()
        latest = {}
        for values_row in values[1:]:
            if not values_row or not values_row[0]:
                continue
            row = dict(zip(COLUMNS, values_row + [''] * (len(COLUMNS) - len(values_row))))
            row['members'] = json.loads(row.pop('members_json'))
            row['files'] = json.loads(row.pop('files_json') or '[]')
            row['invite_emails'] = json.loads(row.pop('invite_emails_json') or '[]')
            # Append order is authoritative; every edit is an immutable revision.
            latest[row['event_id']] = row
        return list(latest.values())

    def save(self, event, expected_revision=None):
        if st.session_state.get('hank_role') not in ('coach', 'athlete'):
            raise PermissionError('Prihlás sa.')
        validate_event(event)
        current = next((e for e in self.all() if e['event_id'] == event['event_id']), None)
        if current:
            if current['revision_id'] != expected_revision:
                raise ValueError('Záznam sa medzičasom zmenil. Obnov stránku a skús znova.')
            if not can_edit(current, member_scope(), actor(), is_coach()):
                raise PermissionError('Tento tréning nemôžeš upravovať.')
        if not is_coach():
            if event['status'] != 'Návrh' or not set(map(norm, event['members'])).issubset(set(map(norm, member_scope()))):
                raise PermissionError('Klient môže ukladať iba vlastný návrh.')
            if event.get('private_notes') or event.get('files') or event.get('invite_emails'):
                raise PermissionError('Prílohy a interné poznámky spravuje tréner.')
        row = dict(event, revision_id=uuid.uuid4().hex, updated_at=datetime.now(timezone.utc).isoformat(), actor=actor())
        row['members_json'] = json.dumps(event['members'], ensure_ascii=False)
        row['files_json'] = json.dumps(event.get('files', []), ensure_ascii=False)
        row['invite_emails_json'] = json.dumps(event.get('invite_emails', []))
        self.worksheet().append_row([row.get(k, '') for k in COLUMNS], value_input_option='RAW')
        return row


def storage_config():
    cfg = dict(st.secrets.get('portal_storage', {}))
    return cfg if cfg.get('url') and cfg.get('service_role_key') and cfg.get('bucket') else None


def storage_request(method, key, data=None, mime=None):
    cfg = storage_config()
    if not cfg:
        raise ValueError('Súkromné úložisko príloh nie je nastavené.')
    base = cfg['url'].rstrip('/')
    if not re.fullmatch(r'https://[a-z0-9-]+\.supabase\.co', base):
        raise ValueError('Neplatná adresa Supabase projektu.')
    # Object names are generated by this server; never accept arbitrary URLs or paths.
    if not re.fullmatch(r'[0-9a-f]{32}/[0-9a-f]{32}\.(pdf|pptx)', key):
        raise ValueError('Neplatná cesta prílohy.')
    headers = {'Authorization': 'Bearer ' + cfg['service_role_key'], 'apikey': cfg['service_role_key']}
    if mime:
        headers['Content-Type'] = mime
    r = requests.request(method, f'{base}/storage/v1/object/{quote(cfg["bucket"], safe="")}/{key}',
                         headers=headers, data=data, timeout=35, allow_redirects=False)
    r.raise_for_status()
    return r.content


def validate_file(filename, data):
    ext = filename.rsplit('.', 1)[-1].lower()
    if not data or len(data) > MAX_BYTES:
        raise ValueError('Príloha musí mať najviac 10 MB.')
    if ext == 'pdf' and data.startswith(b'%PDF-'):
        return ext, 'application/pdf'
    if ext == 'pptx':
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
                if sum(x.file_size for x in z.infolist()) > 100 * 1024 * 1024:
                    raise ValueError('Rozbalená príloha je príliš veľká.')
                if '[Content_Types].xml' in names and 'ppt/presentation.xml' in names and not any('vbaproject' in n.lower() for n in names):
                    return ext, 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
        except zipfile.BadZipFile:
            pass
    raise ValueError('Nahraj platný PDF alebo PPTX bez makier.')


def upload_file(event, file):
    if not is_coach():
        raise PermissionError('Prílohy nahráva tréner.')
    data = file.getvalue()
    ext, mime = validate_file(file.name, data)
    key = event['event_id'] + '/' + uuid.uuid4().hex + '.' + ext
    storage_request('POST', key, data, mime)
    return {'name': file.name[:180], 'key': key, 'mime': mime}


def download_file(event_id, key, store):
    # Re-read and authorize even when the caller supplies an event ID or storage key.
    event = next((e for e in store.all() if e['event_id'] == event_id), None)
    if not event or not can_read(event, member_scope(), is_coach()):
        raise PermissionError('Príloha nie je prístupná.')
    if key not in [f['key'] for f in event['files']]:
        raise PermissionError('Príloha nepatrí k tréningu.')
    return storage_request('GET', key)


def email_addresses(event):
    # Coach may enter recipients; clients cannot set or modify this field.
    explicit = event.get('invite_emails', [])
    if explicit:
        for email in explicit:
            if not re.fullmatch(r'[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+', str(email)):
                raise ValueError('Neplatná adresa príjemcu.')
        return sorted(set(str(e).lower() for e in explicit))
    mapping = dict(st.secrets.get('PORTAL_EMAILS', {}))
    addresses = set()
    for name in event['members']:
        for email in mapping.get(name, []):
            if not re.fullmatch(r'[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+', str(email)):
                raise ValueError('Neplatný e-mail v PORTAL_EMAILS.')
            addresses.add(str(email).lower())
    return sorted(addresses)


def google_event_body(event, recipients):
    return {'summary': 'HANK SC – tréning', 'location': event['location'],
            'description': 'Termín tréningu HANK SC. Podrobnosti a protokoly sú v súkromnom portáli.',
            'start': {'dateTime': event['start'], 'timeZone': 'Europe/Bratislava'},
            'end': {'dateTime': event['end'], 'timeZone': 'Europe/Bratislava'},
            'status': 'cancelled' if event['status'] == 'Zrušené' else 'confirmed',
            'attendees': [{'email': email} for email in recipients]}


def send_google_invite(event):
    if not is_coach() or event['status'] == 'Návrh':
        raise PermissionError('Pozvánku odosiela tréner pre potvrdený termín.')
    cfg = dict(st.secrets.get('google_calendar', {}))
    if not all(cfg.get(k) for k in ('client_id', 'client_secret', 'refresh_token', 'calendar_id')):
        raise ValueError('Google Calendar ešte nie je prepojený.')
    recipients = email_addresses(event)
    if not recipients:
        raise ValueError('Pre vybraných športovcov chýbajú overené kontakty v PORTAL_EMAILS.')
    creds = UserCredentials(None, refresh_token=cfg['refresh_token'],
                            token_uri='https://oauth2.googleapis.com/token',
                            client_id=cfg['client_id'], client_secret=cfg['client_secret'])
    session = AuthorizedSession(creds)
    event_id = hashlib.sha256(event['event_id'].encode()).hexdigest()
    endpoint = 'https://www.googleapis.com/calendar/v3/calendars/' + quote(cfg['calendar_id'], safe='') + '/events'
    url = endpoint + '/' + event_id
    r = session.get(url, timeout=25)
    body = google_event_body(event, recipients)
    if r.status_code == 404:
        if event['status'] == 'Zrušené':
            return 'Zrušené bez Google udalosti'
        r = session.post(endpoint, json=dict(body, id=event_id), params={'sendUpdates': 'all'}, timeout=25)
        if r.status_code == 409:
            r = session.patch(url, json=body, params={'sendUpdates': 'all'}, timeout=25)
    else:
        r.raise_for_status()
        r = session.patch(url, json=body, params={'sendUpdates': 'all'}, timeout=25)
    r.raise_for_status()
    return 'Odoslané ' + datetime.now(TZ).strftime('%d.%m.%Y %H:%M')


def ics_data(event):
    def esc(s):
        return str(s).replace('\\', '\\\\').replace('\n', '\\n').replace(';', '\\;').replace(',', '\\,').replace('\r', '')
    def utc(t):
        return datetime.fromisoformat(t).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//HANK SC//Portal//SK', 'BEGIN:VEVENT',
             'UID:' + event['event_id'] + '@hanksc.com',
             'DTSTAMP:' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'),
             'DTSTART:' + utc(event['start']), 'DTEND:' + utc(event['end']),
             'SUMMARY:HANK SC - trening', 'LOCATION:' + esc(event['location']),
             'STATUS:' + ('CANCELLED' if event['status'] == 'Zrušené' else 'CONFIRMED'),
             'END:VEVENT', 'END:VCALENDAR']
    # Fold UTF-8 physical lines at 75 octets without splitting code points.
    folded = []
    for line in lines:
        part = ''
        for ch in line:
            if len((part + ch).encode()) > 75:
                folded.append(part); part = ' ' + ch
            else:
                part += ch
        folded.append(part)
    return ('\r\n'.join(folded) + '\r\n').encode()


def calendar_link(event):
    def utc(t):
        return datetime.fromisoformat(t).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    return 'https://calendar.google.com/calendar/render?' + urlencode({
        'action': 'TEMPLATE', 'text': 'HANK SC – tréning',
        'dates': utc(event['start']) + '/' + utc(event['end']), 'location': event['location']})


def event_form(store, event=None):
    coach = is_coach()
    if event and not can_edit(event, member_scope(), actor(), coach):
        return
    key = event['event_id'] if event else 'new'
    if not event and 'portal_draft_id' not in st.session_state:
        st.session_state['portal_draft_id'] = uuid.uuid4().hex
    with st.form('portal_form_' + key):
        names = roster() if coach else member_scope()
        members = st.multiselect('Športovci', names, default=event['members'] if event else names[:1]) if coach else names
        title = st.text_input('Názov tréningu', value=event['title'] if event else 'Tréning HANK SC', max_chars=200)
        start0 = datetime.fromisoformat(event['start']).astimezone(TZ) if event else local_datetime(st.session_state.get('portal_day', datetime.now(TZ).date()), time(16, 0))
        cols = st.columns(3)
        day = cols[0].date_input('Dátum', value=start0.date())
        clock = cols[1].time_input('Čas', value=start0.time().replace(tzinfo=None))
        duration = cols[2].number_input('Minúty', min_value=5, max_value=480, value=int((datetime.fromisoformat(event['end']) - start0).total_seconds() / 60) if event else 60, step=5)
        location = st.text_input('Miesto', value=event['location'] if event else '', max_chars=200)
        status = st.selectbox('Stav', ['Návrh', 'Potvrdené', 'Absolvované', 'Zrušené'], index=['Návrh', 'Potvrdené', 'Absolvované', 'Zrušené'].index(event['status']) if event else 1) if coach else 'Návrh'
        notes = st.text_area('Tréningové pokyny / zdieľaný záznam', value=event.get('training_notes', '') if event else '', max_chars=10000)
        private = st.text_area('Interná poznámka – iba tréner', value=event.get('private_notes', '') if event else '', max_chars=10000) if coach else ''
        invite_emails = []
        if coach:
            email_text = st.text_input('E-mail rodiča / klienta pre pozvánku (voliteľné)', value=', '.join(event.get('invite_emails', [])) if event else '', help='Viac adries oddeľ čiarkou. Adresu si s klientom vopred potvrď. E-mail sa odošle až samostatným tlačidlom v detaile.')
            invite_emails = [x.strip().lower() for x in re.split(r'[,;]', email_text) if x.strip()]
        st.caption('Časové pásmo Europe/Bratislava. Klientsky návrh potvrdzuje tréner. Uloženie samo neodosiela e-mail.')
        submitted = st.form_submit_button('Uložiť tréning' if coach else 'Navrhnúť termín', use_container_width=True)
    if submitted:
        try:
            start = local_datetime(day, clock)
            end = (start.astimezone(timezone.utc) + timedelta(minutes=int(duration))).astimezone(TZ)
            item = dict(event or {}, event_id=event['event_id'] if event else st.session_state['portal_draft_id'],
                        members=members, title=title.strip(), start=start.isoformat(), end=end.isoformat(),
                        location=location, status=status, training_notes=notes, private_notes=private, invite_emails=invite_emails,
                        files=event.get('files', []) if event else [], invite_status='')
            email_addresses(item)  # Validate recipients before persisting any change.
            # Block confirmed overlapping coach bookings; a proposal may overlap.
            if status in ('Potvrdené', 'Absolvované'):
                for other in store.all():
                    if other['event_id'] != item['event_id'] and other['status'] in ('Potvrdené', 'Absolvované') and start < datetime.fromisoformat(other['end']) and end > datetime.fromisoformat(other['start']):
                        raise ValueError('Čas koliduje s iným potvrdeným tréningom. Spoločný tréning ulož ako jednu udalosť s viacerými športovcami.')
            store.save(item, event.get('revision_id') if event else None)
            if not event:
                st.session_state.pop('portal_draft_id', None)
            st.success('Uložené. Pozvánku môže tréner odoslať v detaile udalosti.'); st.rerun()
        except Exception as exc:
            st.error(str(exc) if isinstance(exc, (ValueError, PermissionError)) else 'Uloženie sa nepotvrdilo. Obnov zoznam pred opakovaním, aby nevznikol duplikát.')


def show_detail(event, store):
    start = datetime.fromisoformat(event['start']).astimezone(TZ)
    with st.expander(f'{start:%d.%m.%Y %H:%M} · {event["title"]} · {event["status"]}'):
        st.write('Športovci: ' + ', '.join(event['members']))
        st.write('Miesto: ' + (event['location'] or 'Neuvedené'))
        if event.get('training_notes'):
            st.text(event['training_notes'])
        if is_coach() and event.get('private_notes'):
            st.info('Interná poznámka: ' + event['private_notes'])
        if event['status'] in ('Potvrdené', 'Absolvované'):
            st.link_button('Pridať do môjho Google kalendára', calendar_link(event))
            st.download_button('Kalendár pre Apple / Outlook (.ics)', ics_data(event),
                               file_name='hanksc_training.ics', mime='text/calendar', key='ics_' + event['event_id'])
            st.caption('Ručné pridanie vytvorí vlastnú kópiu. Zmeny termínu sa do nej automaticky neprenesú.')
        for f in event['files']:
            st.write('📄 ' + f['name'])
            if st.button('Otvoriť / stiahnuť', key='open_' + f['key']):
                try:
                    data = download_file(event['event_id'], f['key'], store)
                    st.download_button('Stiahnuť ' + f['name'], data=data, file_name=f['name'],
                                       mime=f['mime'], key='dl_' + f['key'])
                    if f['mime'] == 'application/pdf':
                        if hasattr(st, 'pdf'):
                            try:
                                st.pdf(data)
                            except Exception:
                                st.caption('Náhľad nie je dostupný. Použi stiahnutie PDF.')
                        else:
                            st.caption('Náhľad vyžaduje Streamlit s podporou PDF. Súbor môžeš stiahnuť.')
                except Exception:
                    st.error('Prílohu sa nepodarilo bezpečne načítať.')
        if is_coach():
            st.caption('Pozvánka: ' + (event.get('invite_status') or 'Neodoslaná / termín bol upravený'))
            recipients = email_addresses(event)
            st.caption('Adresáti: ' + (', '.join(recipients) or 'Nastav PORTAL_EMAILS v Secrets.'))
            confirm = st.checkbox('Odoslať pozvánku alebo aktualizáciu uvedeným adresátom', key='consent_' + event['event_id'])
            if st.button('Odoslať cez Google Calendar', disabled=not confirm or event['status'] == 'Návrh', key='invite_' + event['event_id']):
                try:
                    current = next(e for e in store.all() if e['event_id'] == event['event_id'])
                    if current['revision_id'] != event['revision_id']:
                        raise ValueError('Termín sa zmenil. Obnov stránku pred odoslaním.')
                    result = send_google_invite(current)
                    store.save(dict(current, invite_status=result), current['revision_id'])
                    st.success(result)
                except Exception as exc:
                    st.error(str(exc) if isinstance(exc, (ValueError, PermissionError)) else 'Odoslanie sa nepotvrdilo. Google udalosť už mohla vzniknúť. Opakovanie používa rovnaké ID, ale môže znovu poslať upozornenie.')
            if not storage_config():
                st.caption('Na nahrávanie protokolov nastav súkromné úložisko podľa návodu.')
            else:
                with st.form('upload_' + event['event_id'], clear_on_submit=True):
                    files = st.file_uploader('PDF náhľad a/alebo PPTX originál (najviac 10 MB každý)', type=['pdf', 'pptx'], accept_multiple_files=True)
                    do_upload = st.form_submit_button('Pripojiť protokoly')
                if do_upload and files:
                    try:
                        current = next(e for e in store.all() if e['event_id'] == event['event_id'])
                        if current['revision_id'] != event['revision_id']:
                            raise ValueError('Záznam sa zmenil. Obnov stránku pred nahrávaním.')
                        # Persist each successful upload separately, so partial failures remain visible.
                        for f in files:
                            attachment = upload_file(current, f)
                            current = store.save(dict(current, files=current['files'] + [attachment]), current['revision_id'])
                        st.success('Prílohy uložené.'); st.rerun()
                    except Exception:
                        st.error('Nahrávanie sa nepotvrdilo celé. Obnov záznam a skontroluj prílohy pred ďalším pokusom. Pôvodné prílohy zostali zachované.')
        if can_edit(event, member_scope(), actor(), is_coach()):
            with st.expander('Upraviť tréning'):
                event_form(store, event)


def month_view(events):
    selected = st.session_state.get('portal_day', datetime.now(TZ).date())
    cols = st.columns([1, 5, 1])
    month = st.session_state.get('portal_month', datetime.now(TZ).date().replace(day=1))
    if cols[0].button('‹', key='prev_month'):
        month = (month - timedelta(days=1)).replace(day=1)
    if cols[2].button('›', key='next_month'):
        month = (month + timedelta(days=32)).replace(day=1)
    st.session_state['portal_month'] = month
    cols[1].markdown(f'### {month:%m / %Y}')
    by_day = {}
    for event in events:
        d = datetime.fromisoformat(event['start']).astimezone(TZ).date()
        by_day.setdefault(d, []).append(event)
    for col, name in zip(st.columns(7), ['Po', 'Ut', 'St', 'Št', 'Pi', 'So', 'Ne']):
        col.caption(name)
    for week in calendar.Calendar().monthdatescalendar(month.year, month.month):
        for col, d in zip(st.columns(7), week):
            n = len([e for e in by_day.get(d, []) if e['status'] != 'Zrušené'])
            if col.button(f'{d.day}' + (f' · {n}' if n else ''), key='day_' + d.isoformat(),
                          disabled=d.month != month.month, use_container_width=True,
                          type='primary' if d == selected else 'secondary'):
                st.session_state['portal_day'] = d
                st.rerun()
    return st.session_state.get('portal_day', datetime.now(TZ).date())


def show_portal(page, get_client):
    if st.session_state.get('hank_role') not in ('coach', 'athlete'):
        st.error('Najprv sa prihlás.'); return
    st.markdown('<div class="hank-hero"><div class="hank-kicker">HANK SC / TRAINING SPACE</div>'
                '<h1>' + ('Kalendár tréningov' if 'Kalendár' in page else 'Tréningové protokoly') +
                '</h1><p>Termíny, záznamy a protokoly na jednom mieste.</p></div>', unsafe_allow_html=True)
    store = Store(get_client())
    try:
        events = visible_events(store.all(), member_scope(), is_coach())
    except Exception:
        st.info('Nový portál ešte nie je pripravený alebo sa nedá načítať. Pôvodný readiness funguje nezávisle.')
        if is_coach() and st.button('Inicializovať nový list portálu'):
            try:
                store.initialize(); st.rerun()
            except Exception:
                st.error('Inicializácia sa nepotvrdila. Over prístup k Google Sheets; pôvodné listy sa nemenia.')
        return
    if st.button('Obnoviť záznamy'):
        st.rerun()
    if is_coach():
        selected = st.selectbox('Športovec', ['Všetci'] + roster())
        if selected != 'Všetci':
            events = [e for e in events if norm(selected) in set(map(norm, e['members']))]
    if 'Kalendár' in page:
        day = month_view(events)
        st.subheader(day.strftime('%d. %m. %Y'))
        today = sorted([e for e in events if datetime.fromisoformat(e['start']).astimezone(TZ).date() == day], key=lambda e: e['start'])
        if not today:
            st.caption('Na tento deň nie je uložený tréning.')
        for event in today:
            show_detail(event, store)
        with st.expander('＋ Pridať tréning' if is_coach() else '＋ Navrhnúť termín'):
            event_form(store)
        upcoming = sorted([e for e in events if datetime.fromisoformat(e['start']).astimezone(TZ).date() >= datetime.now(TZ).date() and e['status'] != 'Zrušené'], key=lambda e: e['start'])
        with st.expander('Najbližšie termíny'):
            for e in upcoming[:10]:
                st.write(datetime.fromisoformat(e['start']).astimezone(TZ).strftime('%d.%m. %H:%M') + ' · ' + e['title'] + ' · ' + e['status'])
    else:
        needle = st.text_input('Hľadať v názve a tréningovom zázname').casefold().strip()
        for event in sorted(events, key=lambda e: e['start'], reverse=True):
            if not needle or needle in (event['title'] + ' ' + event.get('training_notes', '')).casefold():
                show_detail(event, store)
        if not events:
            st.info('Zatiaľ tu nie sú uložené tréningy. Pridaj prvý cez kalendár.')
