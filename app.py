"""Interface local Streamlit do Lead Intelligence Pro."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from leadpro.core import dns_mx_status
from leadpro.storage import (CRM_STATUSES, company_detail, connect, export_csv,
                             import_csv, lead_row, list_companies, save_template,
                             statistics, update_contact, update_crm)

st.set_page_config(page_title='Lead Intelligence Pro', page_icon='📋', layout='wide')
DATA = Path('data')
DB = DATA / 'leadpro.sqlite'
db = connect(DB)

with st.sidebar:
    st.title('Lead Intelligence Pro')
    screen = st.radio('Navegação', ['Dashboard', 'Importar CSV', 'Empresas e contatos',
                                   'Editor de mensagens', 'CRM', 'Exportar'], label_visibility='collapsed')
    st.caption('Dados locais • Contatos não são automaticamente confirmados')

st.title(screen)
if screen == 'Dashboard':
    stats = statistics(db)
    cols = st.columns(4)
    for col, (key, title) in zip(cols, [('empresas','Empresas'),('socios','Sócios'),('telefones','Telefones'),('emails','E-mails')]):
        col.metric(title, f"{stats[key]:,}".replace(',', '.'))
    cols2 = st.columns(3)
    for col, (key, title) in zip(cols2, [('telefones_vinculo_confirmado','Telefones com vínculo confirmado'),
                                        ('emails_vinculo_confirmado','E-mails com vínculo confirmado'),
                                        ('nao_contatar','Não contatar')]):
        col.metric(title, stats[key])
    st.info('Formato válido ≠ linha ativa ≠ titularidade comprovada ≠ existência de WhatsApp. Registros importados não são confirmados automaticamente.')
    st.subheader('Distribuição por segmento')
    rows = db.execute('SELECT segmento,COUNT(*) qtd FROM companies GROUP BY segmento ORDER BY qtd DESC').fetchall()
    if rows:
        st.bar_chart([{'segmento': x['segmento'], 'quantidade': x['qtd']} for x in rows], x='segmento', y='quantidade')

elif screen == 'Importar CSV':
    st.write('Importe `dados_sc.csv` ou outro CSV; os registros já importados são consolidados por **CNPJ completo**.')
    upload = st.file_uploader('Escolher CSV', type=['csv'])
    default_path = 'dados_sc.csv' if Path('dados_sc.csv').exists() else ''
    path = st.text_input('Ou caminho de um arquivo no computador que executa o programa', default_path)
    source = None
    if upload is not None:
        (DATA / 'uploads').mkdir(parents=True, exist_ok=True)
        source = DATA / 'uploads' / Path(upload.name).name
        source.write_bytes(upload.getvalue())
        st.caption(f'Arquivo salvo localmente em {source}; a pasta data/ não deve ser publicada no GitHub.')
    elif path.strip():
        source = Path(path.strip().strip('"'))
    if st.button('Importar / retomar', type='primary', disabled=source is None):
        with st.spinner('Importando...'):
            try:
                result = import_csv(db, source)
                st.success(f"Importação concluída: {result['processed']} linhas novas; {result['rejected']} rejeitadas; {result['companies']} empresas únicas no banco.")
                st.json(result)
            except Exception as exc:
                st.error(f'Não foi possível importar: {exc}')
    st.caption('Retomada automática entre lotes de 500 linhas. Arquivos alterados são reprocessados sem apagar o histórico do CRM.')

elif screen == 'Empresas e contatos':
    search = st.text_input('Pesquisar CNPJ, razão social ou fantasia')
    segment = st.selectbox('Segmento', ['Todos','Advocacia','Imobiliária','Cobrança','Outros'])
    rows = list_companies(db, search, segment, limit=700)
    if not rows:
        st.warning('Nenhuma empresa encontrada. Importe uma planilha primeiro.')
    else:
        st.dataframe(rows, hide_index=True, use_container_width=True)
        selection = st.selectbox('Abrir ficha empresarial', options=[r['cnpj'] for r in rows],
                                 format_func=lambda c: next((f"{r['razao_social']} ({c})" for r in rows if r['cnpj']==c), c))
        detail = company_detail(db, selection)
        st.subheader(detail['razao_social'])
        st.write(f"**CNPJ:** {selection}  |  **Local:** {detail['municipio']}/{detail['uf']}  |  **Atividade:** {detail['segmento']}")
        st.write('**Sócios cadastrados:**', ', '.join(detail['partners']) or 'Não informados')
        with st.expander('Site associado à empresa'):
            with st.form('site'):
                website = st.text_input('URL do site (informe apenas após conferir)', detail['website'])
                if st.form_submit_button('Salvar site'):
                    db.execute('UPDATE companies SET website=? WHERE cnpj=?', (website.strip(), selection))
                    db.commit()
                    st.success('Site registrado. Isso não confirma automaticamente nenhum telefone.')
        st.subheader('Contatos')
        if not detail['contacts']:
            st.info('Nenhum contato disponível no CSV.')
        for contact in detail['contacts']:
            st.write(f"**#{contact['id']} {contact['kind']}**: {contact['original']} → `{contact['normalized'] or 'não normalizado'}`")
            st.caption(f"Formato: {contact['format_status']} • Vínculo: {contact['link_status']} • Atividade: {contact['activity_status']} • MX: {contact['mx_status']}")
            with st.form(f"contact_{contact['id']}"):
                status = st.selectbox('Status do vínculo', ['NAO_VERIFICADO','CONFIRMADO_MANUALMENTE','CONFLITANTE','INVALIDO'],
                                      index=['NAO_VERIFICADO','CONFIRMADO_MANUALMENTE','CONFLITANTE','INVALIDO'].index(contact['link_status']),
                                      key=f"link_{contact['id']}")
                activity = st.selectbox('Atividade do contato (somente com prova)',
                    ['NAO_VERIFICADO','CONFIRMADO_MANUALMENTE','INATIVO_CONFIRMADO'],
                    index=['NAO_VERIFICADO','CONFIRMADO_MANUALMENTE','INATIVO_CONFIRMADO'].index(contact['activity_status']),
                    key=f"activity_{contact['id']}")
                source = st.text_input('Fonte/evidência (ex.: URL e data)', value=contact['source'], key=f"source_{contact['id']}")
                if st.form_submit_button('Salvar verificação manual'):
                    if status == 'CONFIRMADO_MANUALMENTE' and (not source.strip() or source == 'CSV_IMPORTADO'):
                        st.error('Informe evidência independente e verificável para confirmar vínculo.')
                    else:
                        update_contact(db, contact['id'], status, source.strip(), activity)
                        st.success('Verificação registrada.')
            if contact['kind'] == 'EMAIL' and contact['normalized'] and st.button('Consultar DNS/MX do domínio', key=f"mx_{contact['id']}"):
                result = dns_mx_status(contact['normalized'])
                update_contact(db, contact['id'], contact['link_status'], contact['source'], contact['activity_status'], result)
                st.info(f'MX: {result}. Isso não confirma a caixa postal.')

elif screen == 'Editor de mensagens':
    st.write('Crie quantos modelos quiser; as variáveis são preenchidas somente com dados disponíveis.')
    existing = [dict(r) for r in db.execute('SELECT * FROM templates ORDER BY name')]
    chosen = st.selectbox('Editar modelo', ['Novo modelo'] + [r['name'] for r in existing])
    default = next((r for r in existing if r['name']==chosen), {'name':'','body':'','segment':'Geral'})
    with st.form('template_form'):
        name = st.text_input('Nome', value=default['name'])
        segment = st.selectbox('Segmento padrão', ['Geral','Advocacia','Imobiliária','Cobrança','Outros'],
                               index=['Geral','Advocacia','Imobiliária','Cobrança','Outros'].index(default['segment'] if default['segment'] in ['Geral','Advocacia','Imobiliária','Cobrança','Outros'] else 'Geral'))
        body = st.text_area('Mensagem', value=default['body'], height=180)
        if st.form_submit_button('Salvar modelo'):
            try:
                save_template(db, name, body, segment)
                st.success('Modelo salvo.')
            except ValueError as exc:
                st.error(str(exc))
    st.caption('Variáveis: {empresa}, {razao_social}, {nome_fantasia}, {socio}, {primeiro_nome}, {municipio}, {uf}, {segmento}, {meu_nome}, {minha_empresa}, {meu_servico}.')

elif screen == 'CRM':
    search = st.text_input('Buscar empresa')
    rows = list_companies(db, search, limit=500)
    if not rows:
        st.info('Importe leads para utilizar o CRM.')
    else:
        selected = st.selectbox('Empresa', [r['cnpj'] for r in rows],
                                format_func=lambda c: next((r['razao_social'] for r in rows if r['cnpj']==c),c))
        data = company_detail(db, selected)
        templates = [dict(r) for r in db.execute('SELECT * FROM templates ORDER BY name')]
        ids = [None] + [r['id'] for r in templates]
        names = ['Automático pelo segmento'] + [r['name'] for r in templates]
        with st.form('crm_form'):
            status = st.selectbox('Etapa de atendimento', CRM_STATUSES, index=CRM_STATUSES.index(data['status']))
            recipient = st.text_input('Destinatário preferencial', data['recipient'])
            template_id = st.selectbox('Modelo de mensagem', ids, format_func=lambda i: names[ids.index(i)],
                                       index=ids.index(data['template_id']) if data['template_id'] in ids else 0)
            note = st.text_area('Observações', data['note'])
            block = st.checkbox('Não contatar', data['do_not_contact'] == 1)
            contacted = st.checkbox('Confirmo que fiz contato manualmente agora', False)
            if st.form_submit_button('Salvar CRM'):
                update_crm(db, selected, status, note, recipient, template_id, block, contacted)
                st.success('CRM salvo.')
        st.subheader('Prévia da abordagem')
        sender = st.text_input('Seu nome', 'Mikael')
        lead = lead_row(db, selected, sender=sender)
        st.text_area('Mensagem pronta', lead['mensagem_personalizada'], height=140)
        if lead['link_whatsapp']:
            st.link_button('Abrir no WhatsApp (sem envio automático)', lead['link_whatsapp'])
        else:
            st.warning('Link indisponível: telefone sem formato adequado ou empresa na lista Não contatar.')

elif screen == 'Exportar':
    st.write('Gera um CSV com contatos, sócios, classificação, mensagens personalizadas e links WhatsApp.')
    sender = st.text_input('Seu nome nas mensagens', 'Mikael')
    business = st.text_input('Sua empresa (opcional)', '')
    segment = st.selectbox('Segmento', ['Todos','Advocacia','Imobiliária','Cobrança','Outros'])
    score = st.slider('Score cadastral mínimo', 0, 100, 0, 5)
    destination = DATA / 'exports' / 'leads_sc.csv'
    if st.button('Gerar CSV', type='primary'):
        with st.spinner('Gerando arquivo...'):
            count = export_csv(db, destination, min_score=score, segment=segment, sender=sender, business=business)
        st.success(f'{count} empresas exportadas.')
    if destination.is_file():
        st.download_button('Baixar leads_sc.csv', destination.read_bytes(), file_name='leads_sc.csv', mime='text/csv')
    st.warning('Os links do WhatsApp apenas preenchem o texto. Os registros não confirmados continuam sinalizados como não verificados.')
