"""Reimbursement amounts are invoice totals; FX quotes are CNY per 100 units."""
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from django.utils import timezone

CURRENCIES = ('CNY','USD','EUR','GBP','JPY','HKD','MOP','AUD','CAD','CHF','SGD','NZD','KRW','THB','RUB','DKK','NOK','SEK','PHP','MYR','IDR','INR','ZAR','BRL','AED','SAR','TWD','TRY')


def decimal_amount(raw, maximum):
    try:
        value = Decimal(str(raw))
        if not value.is_finite() or value <= 0 or value > maximum or value.as_tuple().exponent < -2:
            raise ValueError('Use a positive value with at most two decimal places')
        return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise ValueError('Invalid amount')


def create_reimbursement(project, actor, body, clean_text):
    from Chat.models import ResearchEvidence
    from Message.models import MediaResource, MediaAsset
    start, end = date.fromisoformat(body['start']), date.fromisoformat(body['end'])
    if end < start or start.year < 1900 or end.year > 2099:
        raise ValueError('Invalid reimbursement dates')
    items = body.get('items')
    if not isinstance(items, list) or not 1 <= len(items) <= 50:
        raise ValueError('Choose 1–50 reimbursement items')
    result, evidence, total = [], set(), Decimal('0.00')
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('Invalid reimbursement item')
        amount = decimal_amount(item.get('amount'), Decimal('10000000'))
        currency = item.get('currency')
        if currency not in CURRENCIES:
            raise ValueError('Unsupported currency')
        rate = Decimal('100.00') if currency == 'CNY' else decimal_amount(item.get('exchange_rate'), Decimal('100000000'))
        converted = (amount * rate / 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        payment, invoice, other = item.get('payment_id'), item.get('invoice_id'), item.get('other_ids', [])
        if type(payment) is not int or type(invoice) is not int or payment == invoice or not isinstance(other, list) or len(other) > 6 or any(type(n) is not int for n in other):
            raise ValueError('Payment record and invoice or receipt are required')
        documents = [payment, invoice, *other]
        if len(documents) != len(set(documents)):
            raise ValueError('Duplicate item documents')
        evidence.update(documents)
        total += converted
        result.append(dict(id=str(uuid.uuid4()), title=clean_text(item,'title',160), amount=f'{amount:.2f}', currency=currency, exchange_rate=f'{rate:.2f}', exchange_rate_basis=100, cny_amount=f'{converted:.2f}', payment_id=payment, invoice_id=invoice, other_ids=other, notes=clean_text(item,'notes',4000,False)))
    if total <= 0 or total > 10000000:
        raise ValueError('Invalid total reimbursement amount')
    media = list(MediaResource.objects.filter(pk__in=evidence,owner=actor,library_active=True,asset__status=MediaAsset.STATUS_READY).select_related('asset'))
    if len(media) != len(evidence):
        raise ValueError('Uploaded documents unavailable')
    identifier = str(uuid.uuid4())
    row = dict(id=identifier,user_id=actor.pk,title=clean_text(body,'title',160),start=start.isoformat(),end=end.isoformat(),amount=f'{total:.2f}',currency='CNY',items=result,notes=clean_text(body,'notes',4000,False),evidence=sorted(evidence),status='submitted',created_at=timezone.now().isoformat())
    for document in media:
        ResearchEvidence.objects.create(project=project,reimbursement_id=identifier,media=document)
        document.recalculate_reference_count()
    return row
