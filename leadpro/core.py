"""Validação conservadora, templates e CSV do Lead Intelligence Pro.

Nenhuma rotina afirma atividade de linha, existência no WhatsApp ou titularidade
sem confirmação externa/autorizada e evidências.
"""
from __future__ import annotations

import csv
import re
import unicodedata
from pathlib import Path
from urllib.parse import quote

DIGITS = re.compile(r'\D+')
VARIABLE = re.compile(r'\{([a-z_]+)\}')


def only_digits(value: object) -> str:
    return DIGITS.sub('', str(value or ''))


def valid_cnpj(value: object) -> bool:
    digits = only_digits(value)
    if len(digits) != 14 or len(set(digits)) == 1:
        return False
    for size, weights in ((12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]),
                          (13, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])):
        rest = sum(int(digits[i]) * weights[i] for i in range(size)) % 11
        expected = 0 if rest < 2 else 11 - rest
        if expected != int(digits[size]):
            return False
    return True


def normalize_phone(value: object) -> dict[str, str]:
    original = str(value or '').strip()
    raw = only_digits(original)
    if not raw:
        return {'normalized': '', 'format_status': 'AUSENTE', 'phone_type': 'DESCONHECIDO'}
    try:
        import phonenumbers
        parsed = phonenumbers.parse(original if original.startswith('+') else '+' + raw if raw.startswith('55') and len(raw) in (12, 13) else original, 'BR')
        if parsed.country_code != 55 or not phonenumbers.is_valid_number(parsed):
            raise ValueError('invalid')
        number = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        t = phonenumbers.number_type(parsed)
        kind = ('MOVEL' if t == phonenumbers.PhoneNumberType.MOBILE else
                'FIXO' if t == phonenumbers.PhoneNumberType.FIXED_LINE else 'OUTRO')
        return {'normalized': number.lstrip('+'), 'format_status': 'FORMATO_VALIDO', 'phone_type': kind}
    except ImportError:
        # Fallback restrito se o usuário ainda não instalou phonenumbers.
        local = raw[2:] if raw.startswith('55') and len(raw) in (12, 13) else raw
        plausible = len(local) in (10, 11) and local[:2] != '00' and local[2] in '23459'
        kind = 'MOVEL' if len(local) == 11 and local[2] == '9' else 'FIXO' if len(local) == 10 else 'DESCONHECIDO'
        return {'normalized': '55' + local if plausible else '',
                'format_status': 'FORMATO_PLAUSIVEL' if plausible else 'INVALIDO', 'phone_type': kind}
    except (ValueError, Exception) as exc:
        # phonenumbers pode emitir NumberParseException, derivada de Exception.
        return {'normalized': '', 'format_status': 'INVALIDO', 'phone_type': 'DESCONHECIDO'}


def normalize_email(value: object) -> dict[str, str]:
    address = str(value or '').strip().lower()
    if not address:
        return {'normalized': '', 'format_status': 'AUSENTE'}
    try:
        from email_validator import validate_email, EmailNotValidError
        try:
            result = validate_email(address, check_deliverability=False)
            return {'normalized': result.normalized.lower(), 'format_status': 'SINTAXE_VALIDA'}
        except EmailNotValidError:
            return {'normalized': '', 'format_status': 'INVALIDO'}
    except ImportError:
        valid = bool(re.fullmatch(r'[^\s@]+@[^\s@]+\.[a-zA-Z]{2,}', address))
        return {'normalized': address if valid else '', 'format_status': 'SINTAXE_PLAUSIVEL' if valid else 'INVALIDO'}


def dns_mx_status(email: str) -> str:
    """Confere MX do domínio; NÃO prova que a caixa postal exista."""
    if not email or '@' not in email:
        return 'NAO_APLICAVEL'
    try:
        import dns.resolver
        answers = dns.resolver.resolve(email.rsplit('@', 1)[-1], 'MX', lifetime=4)
        if not answers:
            return 'SEM_MX'
        return 'MX_ENCONTRADO'
    except ImportError:
        return 'DEPENDENCIA_AUSENTE'
    except Exception as exc:
        try:
            import dns.resolver
            if isinstance(exc, (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer)):
                return 'SEM_MX'
        except ImportError:
            pass
        return 'INCONCLUSIVO'


def clean_name(value: object) -> str:
    return ' '.join(str(value or '').strip().split())


def company_segment(cnae: object) -> str:
    code = only_digits(cnae)
    if code.startswith('6911'):
        return 'Advocacia'
    if code.startswith(('6821', '6822')):
        return 'Imobiliária'
    if code.startswith('8291'):
        return 'Cobrança'
    return 'Outros'


def render_message(template: str, company: dict, sender: str = 'Mikael', business: str = '') -> str:
    owner = clean_name(company.get('socio_destinatario') or company.get('socios') or '')
    if ',' in owner:
        owner = owner.split(',', 1)[0]
    company_name = clean_name(company.get('nome_fantasia') or company.get('razao_social') or 'sua empresa')
    values = {
        'empresa': company_name,
        'razao_social': clean_name(company.get('razao_social') or company_name),
        'nome_fantasia': clean_name(company.get('nome_fantasia') or company_name),
        'socio': owner or 'responsável',
        'primeiro_nome': owner.split()[0].title() if owner else 'pessoal',
        'municipio': clean_name(company.get('municipio') or company.get('municipio_nome')),
        'uf': clean_name(company.get('uf')),
        'segmento': clean_name(company.get('segmento') or company_segment(company.get('cnae_principal'))),
        'cnae_descricao': clean_name(company.get('cnae_descricao')),
        'telefone': clean_name(company.get('telefone')),
        'meu_nome': sender,
        'minha_empresa': business,
        'meu_servico': 'pesquisas cadastrais e levantamentos documentais',
    }
    return VARIABLE.sub(lambda m: values.get(m.group(1), ''), template).strip()


def whatsapp_link(phone: str, message: str) -> str:
    number = only_digits(phone)
    if not number or not message.strip() or not number.startswith('55') or len(number) not in (12, 13):
        return ''
    return f'https://wa.me/{number}?text={quote(message, safe="")}'


def lead_score(phone_format: str, phone_link: str, email_format: str, email_link: str, mx: str, website: str) -> int:
    """Score de QUALIDADE CADASTRAL (não de chance de venda)."""
    score = 0
    if phone_format in ('FORMATO_VALIDO', 'FORMATO_PLAUSIVEL'):
        score += 20
    if phone_link == 'CONFIRMADO_MANUALMENTE':
        score += 25
    if email_format in ('SINTAXE_VALIDA', 'SINTAXE_PLAUSIVEL'):
        score += 15
    if email_link == 'CONFIRMADO_MANUALMENTE':
        score += 15
    if mx == 'MX_ENCONTRADO':
        score += 10
    if website:
        score += 15
    return min(score, 100)


def score_priority(score: int) -> str:
    return 'A' if score >= 80 else 'B' if score >= 60 else 'C' if score >= 40 else 'D'


def csv_rows(path: str | Path):
    with open(path, 'r', encoding='utf-8-sig', errors='replace', newline='') as handle:
        head = handle.read(8192)
        handle.seek(0)
        try:
            separator = csv.Sniffer().sniff(head, delimiters=';,\t').delimiter
        except csv.Error:
            separator = ';'
        for row in csv.DictReader(handle, delimiter=separator):
            yield {str(k).strip(): (v or '').strip() for k, v in row.items() if k is not None}
