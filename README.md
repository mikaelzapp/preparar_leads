# Lead Intelligence Pro

Aplicativo **local** para importar dados empresariais de CSV, consolidar por CNPJ, preservar sócios, normalizar telefones/e-mails, organizar campanhas e modelos de mensagem, acompanhar o CRM e exportar links `wa.me` com textos preenchidos.

> **Nota sobre o projeto:** esta é uma implementação funcional publicada a partir de um repositório que estava vazio. Ela **não** é uma cópia recuperada do ZIP anterior nem oferece integração automática com provedores de validação de linha ou pesquisa de titularidade. Nenhuma verificação de atividade, titularidade ou existência no WhatsApp é inventada.

## Baixar sem Git

[**Baixar código-fonte ZIP do GitHub**](https://github.com/mikaelzapp/preparar_leads/archive/refs/heads/main.zip)

Não publique dados de clientes, base CSV, banco SQLite, ou chaves de API nesse repositório público. A pasta `data/` e arquivos `.csv`/`.sqlite` são ignorados pelo Git.

## Instalação Windows 11

1. Extraia o ZIP para uma pasta do computador.
2. Instale [Python 3.12 ou superior](https://www.python.org/downloads/) e habilite **Add Python to PATH**.
3. Execute `instalar.bat` para criar `.venv` e instalar dependências.
4. Execute `iniciar.bat` para abrir o Streamlit no navegador.
5. No menu **Importar CSV**, selecione o `dados_sc.csv` ou informe seu caminho local.

Alternativamente:

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m streamlit run app.py
```

A aplicação não precisa de contas de nuvem para importar, editar mensagens ou gerar relatórios. Uma conexão com a internet é necessária para instalar dependências. A verificação opcional MX também requer acesso DNS.

## Como funciona

- **Importação retomável** em lotes de 500, identificada pelo hash SHA-256 do arquivo; mesma planilha pode ser reaberta sem duplicar CNPJs, contatos ou sócios. Importar novamente um arquivo alterado reprocessa os registros, preservando observações já existentes.
- **Contatos:** `phonenumbers` normaliza e valida formato de telefone brasileiro. `email-validator` verifica sintaxe. Consulta DNS/MX manual opcional. Formato e MX **não comprovam atividade da linha, existência da caixa postal ou vínculo com a empresa**.
- **Evidência manual:** analise o site da empresa ou fonte pública e indique status e URL de suporte. Registros importados ficam `NAO_VERIFICADO` por padrão.
- **Mensagens:** modelos editáveis por segmento com variáveis como `{empresa}`, `{primeiro_nome}`, `{meu_nome}`; gestão por empresa no CRM.
- **WhatsApp:** o `wa.me` **não envia** mensagens; apenas abre uma conversa com texto preenchido. O link tampouco confirma a existência de conta no serviço.
- **Não contatar:** contatos marcados como bloqueados não recebem link de WhatsApp na exportação.
- **Exportação:** `data/exports/leads_sc.csv` e botão de download na interface; CSV com UTF-8 BOM e ponto e vírgula para Excel brasileiro.

## Interface

Dashboard; Importar CSV; Empresas e contatos; Editor de mensagens; CRM; Exportar.

## Segurança e privacidade

O repositório é **público**. Nunca faça commit da sua base `dados_sc.csv`, dados pessoais, `.env`, arquivos SQLite ou leads exportados. A planilha e os bancos devem permanecer no seu computador ou em armazenamento privado com acesso restrito. Prospecção deve respeitar as regras da LGPD e as preferências dos destinatários. Fonte pública não significa uso irrestrito.

## Testes

```powershell
.venv\Scripts\python -m pytest -q
```

Os testes usam dados fictícios e não fazem disparos. O GitHub Actions também roda a suíte em Python 3.12.

## Limitações atuais e próximos módulos

- Não é possível concluir que um telefone está ativo, é da empresa ou possui WhatsApp apenas lendo um CSV. É necessário evidência independente ou provedor com permissão e método adequado.
- Não existe descoberta automática de domínio/site oficial; o site pode ser associado manualmente na ficha.
- DNS/MX confirma apenas infraestrutura do domínio, não a caixa postal.
- A importação e exportação são adequadas para dezenas de milhares de linhas. Para milhões de registros, adicionar paginação server-side, filas de processamento e otimizações para operações em lote.
- A interface não realiza envios automáticos ou contornos de limitação do WhatsApp.

**Repositório:** https://github.com/mikaelzapp/preparar_leads
