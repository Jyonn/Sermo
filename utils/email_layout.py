"""Small, escaped HTML fragments compatible with table-based email clients."""
from html import escape
from urllib.parse import urlsplit


def avatar_url(actor):
    if actor is None:
        return ''
    value = str(actor.tiny_json().get('avatar_uri') or '')
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password else ''
    except ValueError:
        return ''


def text(value):
    return escape(str(value or ''), quote=True).replace('\n', '<br>')


def message_row(name, body, avatar=''):
    initial = text(str(name or '')[:1])
    portrait = (f'<img src="{escape(avatar, quote=True)}" width="40" height="40" alt="" style="display:block;border:0;border-radius:12px">'
                if avatar else f'<span style="display:block;width:40px;height:40px;line-height:40px;text-align:center;background:#e3eee7;border-radius:12px;color:#365a49;font-size:16px">{initial}</span>')
    return f'''<tr><td width="48" valign="top" style="padding:10px 8px 10px 0">{portrait}</td>
<td valign="top" style="padding:8px 0 12px"><div style="font-size:12px;color:#61756a;margin:0 0 6px">{text(name)}</div>
<table role="presentation" cellpadding="0" cellspacing="0" style="max-width:100%"><tr><td style="padding:12px 16px;background:#fffdf8;border:1px solid #dce5de;border-radius:4px 16px 16px;color:#213d30;font-size:15px;line-height:1.7;overflow-wrap:anywhere;word-break:break-word">{text(body)}</td></tr></table></td></tr>'''


def conversation_block(name, count, rows):
    return f'''<section style="margin:0 0 20px"><h3 style="margin:0;padding:14px 16px;border-bottom:1px solid #dce5de;font-size:15px;color:#213d30">{text(name)}<span style="font-size:12px;font-weight:400;color:#61756a"> · {text(count)}</span></h3>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="padding:8px 14px;background:#f0f5ef;border-radius:0 0 16px 16px;table-layout:fixed"><tbody>{''.join(rows)}</tbody></table></section>'''


def email_body(heading, content, note=''):
    return f'''<div style="max-width:600px;margin:0 auto;font-family:'PingFang SC','Microsoft YaHei',sans-serif;color:#213d30;line-height:1.65">
<h2 style="font-size:21px;line-height:1.4;margin:0 0 22px">{text(heading)}</h2>{content}
{f'<p style="font-size:13px;color:#61756a;margin:20px 0 0">{text(note)}</p>' if note else ''}</div>'''
