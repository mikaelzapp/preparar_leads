"""Persistência SQLite, importação retomável, CRM e exportação."""
from __future__ import annotations

import csv
import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .core import (clean_name, company_segment, csv_rows, lead_score, normalize_email,
                   normalize_phone, only_digits, render_message, score_priority,
                   valid_cnpj, whatsapp_link)

DEFAULT_TEMPLATES = {
    'Advocacia': 'Olá! Tudo bem? Sou {meu_nome} e gostaria de apresentar à {empresa} meus serviços de pesquisas cadastrais e levantamentos documentais para apoio a diligências jurídicas. Posso explicar brevemente como funciona?',
    'Imobiliária': 'Olá! Tudo bem? Meu nome é {meu_nome}. Trabalho com pesquisas e levantamentos de informações públicas que podem auxiliar empresas do setor imobiliário. Gostaria de apresentar uma proposta à {empresa}. Posso encaminhar mais detalhes?',
    'Cobrança': 'Olá! Tudo bem? Sou {meu_nome} e ofereço serviços de inteligência cadastral e levantamentos documentais sob demanda. Gostaria de saber se esse tipo de apoio pode ser útil para a {empresa}?',
    'Geral': 'Olá! Tudo bem? Sou {meu_nome} e gostaria de apresentar à {empresa} meus serviços de pesquisas cadastrais e levantamentos documentais. Posso compartilhar mais informações?',
}
CRM_STATUSES = ('NOVO', 'VALIDADO', 'PRONTO_PARA_CONTATO', 'CONTATADO',
                'AGUARDANDO_RESPOSTA', 'INTERESSADO', 'NEGOCIANDO', 'CLIENTE',
                'SEM_INTERESSE', 'NUMERO_INCORRETO', 'NAO_CONTATAR')


def connect(path: str | Path = 'data/leadpro.sqlite') -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS companies (
        cnpj TEXT PRIMARY KEY, valid_cnpj INTEGER NOT NULL DEFAULT 0,
        razao_social TEXT, nome_fantasia TEXT, municipio TEXT, uf TEXT,
        cnae_principal TEXT, cnae_descricao TEXT, segmento TEXT,
        website TEXT NOT NULL DEFAULT '', situacao TEXT, imported_at TEXT
    );
    CREATE TABLE IF NOT EXISTS partners (
        cnpj TEXT NOT NULL REFERENCES companies(cnpj), name TEXT NOT NULL,
        PRIMARY KEY(cnpj, name)
    );
    CREATE TABLE IF NOT EXISTS contacts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cnpj TEXT NOT NULL REFERENCES companies(cnpj),
        kind TEXT NOT NULL CHECK(kind IN ('PHONE','EMAIL')),
        original TEXT NOT NULL, normalized TEXT NOT NULL,
        format_status TEXT NOT NULL, phone_type TEXT NOT NULL DEFAULT '',
        link_status TEXT NOT NULL DEFAULT 'NAO_VERIFICADO',
        activity_status TEXT NOT NULL DEFAULT 'NAO_VERIFICADO',
        mx_status TEXT NOT NULL DEFAULT 'NAO_VERIFICADO',
        source TEXT NOT NULL DEFAULT '', checked_at TEXT NOT NULL DEFAULT '',
        UNIQUE(cnpj, kind, original)
    );
    CREATE INDEX IF NOT EXISTS idx_contacts_cnpj ON contacts(cnpj);
    CREATE TABLE IF NOT EXISTS templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL,
        body TEXT NOT NULL, segment TEXT NOT NULL DEFAULT 'Geral'
    );
    CREATE TABLE IF NOT EXISTS crm (
        cnpj TEXT PRIMARY KEY REFERENCES companies(cnpj),
        status TEXT NOT NULL DEFAULT 'NOVO', note TEXT NOT NULL DEFAULT '',
        recipient TEXT NOT NULL DEFAULT '', template_id INTEGER,
        do_not_contact INTEGER NOT NULL DEFAULT 0, contacted_at TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS import_jobs (
        filepath TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, last_row INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL DEFAULT 'NOVO', updated_at TEXT
    );
    ''')
    for name, body in DEFAULT_TEMPLATES.items():
        db.execute('INSERT OR IGNORE INTO templates (name, body, segment) VALUES (?, ?, ?)', (name, body, name))
    db.commit()
    return db


def _fingerprint(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open('rb') as handle:
        for part in iter(lambda: handle.read(1024 * 1024), b''):
            sha.update(part)
    return sha.hexdigest()


def import_csv(db: sqlite3.Connection, path: str | Path,
               progress: Callable[[int], None] | None = None,
               batch_size: int = 500) -> dict:
    """Retoma um arquivo idêntico a partir do último lote confirmado.

    Novo conteúdo no mesmo caminho reinicia a leitura (UPSERT idempotente).
    Não apaga contatos/empresas antigos; o operador pode escolher DB separado.
    """
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    fingerprint = _fingerprint(path)
    job = db.execute('SELECT fingerprint,last_row FROM import_jobs WHERE filepath=?', (str(path),)).fetchone()
    resume = int(job['last_row']) if job and job['fingerprint'] == fingerprint else 0
    timestamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
    db.execute('''INSERT INTO import_jobs(filepath, fingerprint,last_row,state,updated_at)
                  VALUES (?, ?, ?, 'EXECUTANDO', ?)
                  ON CONFLICT(filepath) DO UPDATE SET fingerprint=excluded.fingerprint,
                  last_row=excluded.last_row,state=excluded.state,updated_at=excluded.updated_at''',
               (str(path), fingerprint, resume, timestamp))
    db.commit()
    processed = 0
    rejected = 0
    last = resume
    for index, row in enumerate(csv_rows(path), 1):
        if index <= resume:
            continue
        last = index
        cnpj = only_digits(row.get('cnpj'))
        if len(cnpj) != 14:
            rejected += 1
        else:
            name = clean_name(row.get('razao_social'))
            fantasy = clean_name(row.get('nome_fantasia'))
            code = only_digits(row.get('cnae_principal'))
            db.execute('''INSERT INTO companies
                (cnpj, valid_cnpj,razao_social,nome_fantasia,municipio,uf,cnae_principal,cnae_descricao,segmento,situacao,imported_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(cnpj) DO UPDATE SET
                valid_cnpj=excluded.valid_cnpj,
                razao_social=CASE WHEN excluded.razao_social!='' THEN excluded.razao_social ELSE companies.razao_social END,
                nome_fantasia=CASE WHEN excluded.nome_fantasia!='' THEN excluded.nome_fantasia ELSE companies.nome_fantasia END,
                municipio=excluded.municipio,uf=excluded.uf,cnae_principal=excluded.cnae_principal,
                cnae_descricao=excluded.cnae_descricao,segmento=excluded.segmento,
                situacao=excluded.situacao,imported_at=excluded.imported_at''',
                (cnpj, int(valid_cnpj(cnpj)), name, fantasy, clean_name(row.get('municipio_nome')),
                 clean_name(row.get('uf')), code, clean_name(row.get('cnae_descricao')),
                 company_segment(code), clean_name(row.get('situacao')), timestamp))
            for partner in (row.get('socio_nome'), row.get('socios.nome_socio')):
                person = clean_name(partner)
                if person:
                    db.execute('INSERT OR IGNORE INTO partners(cnpj,name) VALUES (?,?)', (cnpj, person))
            for raw in (row.get('telefone_1'), row.get('telefone_2')):
                raw = clean_name(raw)
                if raw:
                    check = normalize_phone(raw)
                    db.execute('''INSERT OR IGNORE INTO contacts
                        (cnpj,kind,original,normalized,format_status,phone_type,source)
                        VALUES (?,?,?,?,?,?,?)''',
                        (cnpj, 'PHONE', raw, check['normalized'], check['format_status'], check['phone_type'], 'CSV_IMPORTADO'))
            raw = clean_name(row.get('email'))
            if raw:
                check = normalize_email(raw)
                db.execute('''INSERT OR IGNORE INTO contacts
                    (cnpj,kind,original,normalized,format_status,source) VALUES (?,?,?,?,?,?)''',
                    (cnpj, 'EMAIL', raw, check['normalized'], check['format_status'], 'CSV_IMPORTADO'))
            db.execute('INSERT OR IGNORE INTO crm(cnpj) VALUES (?)', (cnpj,))
            processed += 1
        if index % batch_size == 0:
            db.execute('UPDATE import_jobs SET last_row=?,updated_at=? WHERE filepath=?',
                       (index, timestamp, str(path)))
            db.commit()
            if progress:
                progress(index)
    db.execute('UPDATE import_jobs SET last_row=?,state=?,updated_at=? WHERE filepath=?',
               (last, 'CONCLUIDO', datetime.now(timezone.utc).isoformat(timespec='seconds'), str(path)))
    db.commit()
    if progress:
        progress(last)
    return {'processed': processed, 'rejected': rejected, 'resumed_after': resume,
            'last_row': last, 'companies': db.execute('SELECT COUNT(*) FROM companies').fetchone()[0]}


def company_detail(db: sqlite3.Connection, cnpj: str) -> dict:
    row = db.execute('''SELECT companies.*,crm.status,crm.note,crm.recipient,crm.template_id,crm.do_not_contact,crm.contacted_at
        FROM companies JOIN crm USING(cnpj) WHERE companies.cnpj=?''', (cnpj,)).fetchone()
    if not row:
        return {}
    data = dict(row)
    data['partners'] = [r['name'] for r in db.execute('SELECT name FROM partners WHERE cnpj=? ORDER BY name', (cnpj,))]
    data['contacts'] = [dict(r) for r in db.execute('SELECT * FROM contacts WHERE cnpj=? ORDER BY kind,id', (cnpj,))]
    data['socios'] = ', '.join(data['partners'])
    data['socio_destinatario'] = data['recipient'] or (data['partners'][0] if data['partners'] else '')
    return data


def _best_contact(contacts: list[dict], kind: str) -> dict:
    options = [x for x in contacts if x['kind'] == kind]
    acceptable = (('FORMATO_VALIDO', 'FORMATO_PLAUSIVEL') if kind == 'PHONE'
                  else ('SINTAXE_VALIDA', 'SINTAXE_PLAUSIVEL'))

    def usable(item: dict) -> bool:
        return (bool(item['normalized']) and item['format_status'] in acceptable
                and item['link_status'] not in ('INVALIDO', 'CONFLITANTE')
                and item['activity_status'] != 'INATIVO_CONFIRMADO')

    # Um contato confirmado como inativo ou conflitante nunca deve ganhar
    # prioridade sobre um contato com formato válido, ainda não verificado.
    options.sort(key=lambda item: (
        usable(item),
        item['link_status'] == 'CONFIRMADO_MANUALMENTE',
        item['format_status'] in acceptable,
        bool(item['normalized'])), reverse=True)
    return options[0] if options else {}


def lead_row(db: sqlite3.Connection, cnpj: str, template: dict | None = None,
             sender: str = 'Mikael', business: str = '') -> dict:
    data = company_detail(db, cnpj)
    if not data:
        return {}
    phone = _best_contact(data['contacts'], 'PHONE')
    email = _best_contact(data['contacts'], 'EMAIL')
    if template is None:
        if data['template_id']:
            saved = db.execute('SELECT * FROM templates WHERE id=?', (data['template_id'],)).fetchone()
        else:
            saved = db.execute('SELECT * FROM templates WHERE name=?', (data['segmento'],)).fetchone()
        if not saved:
            saved = db.execute('SELECT * FROM templates WHERE name=?', ('Geral',)).fetchone()
        template = dict(saved) if saved else {'name':'', 'body':''}
    context = {**data, 'telefone': phone.get('normalized', '')}
    message = render_message(template.get('body', ''), context, sender=sender, business=business)
    score = lead_score(phone.get('format_status',''),phone.get('link_status',''),
                       email.get('format_status',''),email.get('link_status',''),
                       email.get('mx_status',''),data.get('website',''))
    eligible = (not data['do_not_contact'] and data['status'] != 'NAO_CONTATAR' and
                phone.get('format_status') in ('FORMATO_VALIDO','FORMATO_PLAUSIVEL') and
                phone.get('link_status') not in ('INVALIDO', 'CONFLITANTE') and
                phone.get('activity_status') != 'INATIVO_CONFIRMADO')
    return {
        'cnpj': cnpj, 'cnpj_valido': data['valid_cnpj'], 'razao_social': data['razao_social'],
        'nome_fantasia': data['nome_fantasia'], 'socios': data['socios'],
        'socio_destinatario': data['socio_destinatario'], 'municipio': data['municipio'],
        'uf': data['uf'], 'segmento': data['segmento'], 'cnae_principal': data['cnae_principal'],
        'telefone': phone.get('normalized',''), 'telefone_status': phone.get('format_status','AUSENTE'),
        'telefone_vinculo': phone.get('link_status','NAO_VERIFICADO'),
        'telefone_atividade': phone.get('activity_status','NAO_VERIFICADO'),
        'telefone_fonte': phone.get('source',''),
        'email': email.get('normalized',''), 'email_status': email.get('format_status','AUSENTE'),
        'email_vinculo': email.get('link_status','NAO_VERIFICADO'),
        'email_mx': email.get('mx_status','NAO_VERIFICADO'),
        'site': data['website'], 'score_cadastral': score, 'prioridade': score_priority(score),
        'modelo_mensagem': template.get('name',''), 'mensagem_personalizada': message,
        'link_whatsapp': whatsapp_link(phone.get('normalized',''),message) if eligible else '',
        'status_contato': data['status'], 'nao_contatar': data['do_not_contact'],
        'ultima_abordagem': data['contacted_at'], 'observacoes': data['note'],
    }


def list_companies(db: sqlite3.Connection, search: str = '', segment: str = 'Todos',
                   limit: int = 500, offset: int = 0) -> list[dict]:
    query = '''SELECT c.cnpj,c.razao_social,c.nome_fantasia,c.municipio,c.uf,c.segmento,
                     cr.status,cr.do_not_contact FROM companies c JOIN crm cr USING(cnpj) WHERE 1=1'''
    args: list = []
    if search.strip():
        query += ' AND (c.razao_social LIKE ? OR c.nome_fantasia LIKE ? OR c.cnpj LIKE ?)'
        pattern = '%' + search.strip() + '%'
        args.extend([pattern] * 3)
    if segment != 'Todos':
        query += ' AND c.segmento=?'
        args.append(segment)
    query += ' ORDER BY c.razao_social LIMIT ? OFFSET ?'
    args.extend([min(limit, 5000), offset])
    return [dict(r) for r in db.execute(query, args)]


EXPORT_COLUMNS = (
    'cnpj', 'cnpj_valido', 'razao_social', 'nome_fantasia', 'socios',
    'socio_destinatario', 'municipio', 'uf', 'segmento', 'cnae_principal',
    'telefone', 'telefone_status', 'telefone_vinculo', 'telefone_atividade',
    'telefone_fonte', 'email', 'email_status', 'email_vinculo', 'email_mx',
    'site', 'score_cadastral', 'prioridade', 'modelo_mensagem',
    'mensagem_personalizada', 'link_whatsapp', 'status_contato',
    'nao_contatar', 'ultima_abordagem', 'observacoes'
)


def _safe_csv_cell(value: object) -> object:
    """Evita que texto externo seja executado como fórmula ao abrir no Excel."""
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + value
    return value


def export_csv(db: sqlite3.Connection, filepath: str | Path, min_score: int = 0,
               segment: str = 'Todos', sender: str = 'Mikael', business: str = '') -> int:
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    cursor = db.execute('SELECT cnpj FROM companies ORDER BY razao_social,cnpj')
    count = 0
    with path.open('w', newline='', encoding='utf-8-sig') as file:
        writer = csv.DictWriter(file, fieldnames=EXPORT_COLUMNS, delimiter=';', extrasaction='ignore')
        writer.writeheader()
        for item in cursor:
            lead = lead_row(db, item['cnpj'], sender=sender, business=business)
            if lead['score_cadastral'] < min_score or (segment != 'Todos' and lead['segmento'] != segment):
                continue
            writer.writerow({key: _safe_csv_cell(value) for key, value in lead.items()})
            count += 1
    return count


def save_template(db: sqlite3.Connection, name: str, body: str, segment: str = 'Geral') -> None:
    if not name.strip() or not body.strip():
        raise ValueError('Nome e mensagem são obrigatórios')
    db.execute('''INSERT INTO templates(name,body,segment) VALUES(?,?,?)
                ON CONFLICT(name) DO UPDATE SET body=excluded.body,segment=excluded.segment''',
               (name.strip(), body.strip(), segment))
    db.commit()


def update_crm(db: sqlite3.Connection, cnpj: str, status: str, note: str,
               recipient: str = '', template_id: int | None = None, block: bool = False,
               contacted: bool = False) -> None:
    if status not in CRM_STATUSES:
        raise ValueError('Status inválido')
    stamp = datetime.now(timezone.utc).isoformat(timespec='seconds') if contacted else ''
    db.execute('''UPDATE crm SET status=?,note=?,recipient=?,template_id=?,do_not_contact=?,
                contacted_at=CASE WHEN ? != '' THEN ? ELSE contacted_at END WHERE cnpj=?''',
               (status, note, recipient, template_id, int(block or status == 'NAO_CONTATAR'), stamp, stamp, cnpj))
    db.commit()


def update_contact(db: sqlite3.Connection, contact_id: int, link_status: str,
                   source: str = '', activity_status: str = 'NAO_VERIFICADO',
                   mx_status: str | None = None) -> None:
    allowed = {'NAO_VERIFICADO','CONFIRMADO_MANUALMENTE','CONFLITANTE','INVALIDO'}
    if link_status not in allowed or activity_status not in {'NAO_VERIFICADO','CONFIRMADO_MANUALMENTE','INATIVO_CONFIRMADO'}:
        raise ValueError('Verificação inválida')
    if ('CONFIRMADO_MANUALMENTE' in (link_status, activity_status)
            and source.strip().upper() in ('', 'CSV_IMPORTADO')):
        raise ValueError('Confirmação manual exige evidência independente da planilha.')
    stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
    db.execute('''UPDATE contacts SET link_status=?,source=?,activity_status=?,
                mx_status=COALESCE(?,mx_status),checked_at=? WHERE id=?''',
               (link_status,source,activity_status,mx_status,stamp,contact_id))
    db.commit()


def statistics(db: sqlite3.Connection) -> dict:
    return {
        'empresas': db.execute('SELECT COUNT(*) FROM companies').fetchone()[0],
        'socios': db.execute('SELECT COUNT(*) FROM partners').fetchone()[0],
        'telefones': db.execute("SELECT COUNT(*) FROM contacts WHERE kind='PHONE'").fetchone()[0],
        'emails': db.execute("SELECT COUNT(*) FROM contacts WHERE kind='EMAIL'").fetchone()[0],
        'telefones_vinculo_confirmado': db.execute("SELECT COUNT(*) FROM contacts WHERE kind='PHONE' AND link_status='CONFIRMADO_MANUALMENTE'").fetchone()[0],
        'emails_vinculo_confirmado': db.execute("SELECT COUNT(*) FROM contacts WHERE kind='EMAIL' AND link_status='CONFIRMADO_MANUALMENTE'").fetchone()[0],
        'nao_contatar': db.execute('SELECT COUNT(*) FROM crm WHERE do_not_contact=1').fetchone()[0],
    }
