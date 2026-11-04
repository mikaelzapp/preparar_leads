import csv
import sqlite3
from urllib.parse import parse_qs, urlparse

import pytest

from leadpro.core import (company_segment, normalize_email, normalize_phone, render_message,
                          valid_cnpj, whatsapp_link)
from leadpro.storage import (company_detail, connect, export_csv, import_csv, lead_row,
                             save_template, update_contact, update_crm)


@pytest.fixture
def db(tmp_path):
    database = connect(tmp_path / 'test.sqlite')
    yield database
    database.close()


def test_cnpj_checksum():
    assert valid_cnpj('11.222.333/0001-81')
    assert not valid_cnpj('11.222.333/0001-82')
    assert not valid_cnpj('00000000000000')


def test_phone_does_not_claim_activity():
    result = normalize_phone('(47) 99999-0000')
    assert result['normalized'].startswith('5547')
    assert result['format_status'] in ('FORMATO_VALIDO', 'FORMATO_PLAUSIVEL')
    assert normalize_phone('123')['normalized'] == ''


def test_email_syntax():
    assert normalize_email('Contato@Exemplo.com.br')['normalized'] == 'contato@exemplo.com.br'
    assert normalize_email('nao-e-email')['format_status'] == 'INVALIDO'


def test_template_fallback():
    result = render_message('Olá {primeiro_nome}, falo com {empresa}. Sou {meu_nome}.',
                            {'razao_social': 'Empresa A Ltda'}, sender='Mikael')
    assert result == 'Olá pessoal, falo com Empresa A Ltda. Sou Mikael.'


def test_whatsapp_encoding():
    url = whatsapp_link('5547999990000', 'Olá! Ação & pesquisa')
    assert urlparse(url).netloc == 'wa.me'
    assert parse_qs(urlparse(url).query)['text'] == ['Olá! Ação & pesquisa']
    assert whatsapp_link('', 'Oi') == ''


def make_csv(path):
    rows = [
        {'cnpj':'11222333000181', 'razao_social':'EMPRESA A LTDA', 'nome_fantasia':'Empresa A',
         'municipio_nome':'Joinville','uf':'SC','cnae_principal':'6911701','cnae_descricao':'Advocacia',
         'telefone_1':'(47) 99999-0000','telefone_2':'','email':'a@example.com',
         'socio_nome':'ANA TESTE','socios.nome_socio':'ANA TESTE','situacao':'ATIVA'},
        {'cnpj':'11222333000181', 'razao_social':'EMPRESA A LTDA', 'nome_fantasia':'Empresa A',
         'municipio_nome':'Joinville','uf':'SC','cnae_principal':'6911701','cnae_descricao':'Advocacia',
         'telefone_1':'(47) 99999-0000','telefone_2':'(47) 3333-4444','email':'a@example.com',
         'socio_nome':'BRUNO TESTE','socios.nome_socio':'BRUNO TESTE','situacao':'ATIVA'},
    ]
    with path.open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.DictWriter(file, delimiter=';', fieldnames=rows[0].keys())
        writer.writeheader(); writer.writerows(rows)


def test_import_consolidates_partners_and_resumes(db,tmp_path):
    file = tmp_path / 'dados_sc.csv'
    make_csv(file)
    result = import_csv(db, file, batch_size=1)
    assert result['companies'] == 1
    detail = company_detail(db, '11222333000181')
    assert detail['partners'] == ['ANA TESTE','BRUNO TESTE']
    assert len(detail['contacts']) == 3
    second = import_csv(db, file)
    assert second['processed'] == 0
    assert second['resumed_after'] == 2


def test_exports_message_and_respects_dnc(db,tmp_path):
    file = tmp_path / 'dados_sc.csv'
    make_csv(file)
    import_csv(db, file)
    row = lead_row(db,'11222333000181')
    assert 'EMPRESA' in row['razao_social']
    assert row['telefone_atividade'] == 'NAO_VERIFICADO'
    assert row['link_whatsapp']
    out = tmp_path / 'out.csv'
    assert export_csv(db,out) == 1
    assert 'link_whatsapp' in out.read_text(encoding='utf-8-sig')
    update_crm(db, '11222333000181', 'NAO_CONTATAR', 'recusou contato', block=True)
    assert lead_row(db, '11222333000181')['link_whatsapp'] == ''


def test_manual_verification_requires_controlled_status(db,tmp_path):
    file = tmp_path / 'dados_sc.csv'; make_csv(file); import_csv(db,file)
    contact = company_detail(db,'11222333000181')['contacts'][0]
    update_contact(db, contact['id'], 'CONFIRMADO_MANUALMENTE', 'https://empresa.example/contato')
    updated = company_detail(db,'11222333000181')['contacts'][0]
    assert updated['link_status'] == 'CONFIRMADO_MANUALMENTE'
    with pytest.raises(ValueError):
        update_contact(db, contact['id'], 'CONFIRMADO_AUTOMATICAMENTE')


def test_template_can_be_customized(db,tmp_path):
    file = tmp_path / 'dados_sc.csv'; make_csv(file); import_csv(db,file)
    save_template(db, 'Personalizada', 'Oi {primeiro_nome}, sou {meu_nome}.')
    template = dict(db.execute('SELECT * FROM templates WHERE name=?', ('Personalizada',)).fetchone())
    row = lead_row(db,'11222333000181',template=template)
    assert row['mensagem_personalizada'].startswith('Oi Ana,')


def test_nao_contatar_status_alone_blocks_whatsapp(db, tmp_path):
    source = tmp_path / 'dados_sc.csv'
    make_csv(source)
    import_csv(db, source)
    update_crm(db, '11222333000181', 'NAO_CONTATAR', 'solicitou exclusão', block=False)
    lead = lead_row(db, '11222333000181')
    assert lead['nao_contatar'] == 1
    assert lead['link_whatsapp'] == ''


def test_inactive_phone_does_not_hide_other_usable_number(db, tmp_path):
    source = tmp_path / 'dados_sc.csv'
    make_csv(source)
    import_csv(db, source)
    contacts = company_detail(db, '11222333000181')['contacts']
    mobile = next(c for c in contacts if c['kind'] == 'PHONE' and '99999' in c['original'])
    update_contact(db, mobile['id'], 'CONFIRMADO_MANUALMENTE',
                   'Registro de verificação manual', 'INATIVO_CONFIRMADO')
    lead = lead_row(db, '11222333000181')
    assert lead['telefone'].endswith('33334444')
    assert lead['link_whatsapp']


def test_manual_confirmation_requires_independent_source(db, tmp_path):
    source = tmp_path / 'dados_sc.csv'
    make_csv(source)
    import_csv(db, source)
    contact = company_detail(db, '11222333000181')['contacts'][0]
    with pytest.raises(ValueError, match='evidência'):
        update_contact(db, contact['id'], 'CONFIRMADO_MANUALMENTE', 'CSV_IMPORTADO')
    with pytest.raises(ValueError, match='evidência'):
        update_contact(db, contact['id'], 'NAO_VERIFICADO', '', 'CONFIRMADO_MANUALMENTE')


def test_empty_filtered_export_retains_full_header(db, tmp_path):
    source = tmp_path / 'dados_sc.csv'
    make_csv(source)
    import_csv(db, source)
    output = tmp_path / 'filtered.csv'
    assert export_csv(db, output, min_score=100) == 0
    with output.open(encoding='utf-8-sig', newline='') as file:
        rows = list(csv.DictReader(file, delimiter=';'))
        assert rows == []
        file.seek(0)
        header = file.readline()
    assert 'socios' in header
    assert 'mensagem_personalizada' in header


def test_export_quotes_excel_formula_cells(db, tmp_path):
    source = tmp_path / 'dados_sc.csv'
    make_csv(source)
    import_csv(db, source)
    db.execute('UPDATE companies SET nome_fantasia=? WHERE cnpj=?',
               ('=SOMA(1;2)', '11222333000181'))
    db.commit()
    output = tmp_path / 'safe.csv'
    assert export_csv(db, output) == 1
    with output.open(encoding='utf-8-sig', newline='') as file:
        row = next(csv.DictReader(file, delimiter=';'))
    assert row['nome_fantasia'].startswith("'=")
