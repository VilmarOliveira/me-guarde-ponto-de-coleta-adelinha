"""Deterministic, escaped article markup. No external parser or execution of HTML."""
import html
import re
from urllib.parse import urlsplit
from markupsafe import escape

# Only trusted SVG paths from the project; uploaded SVG/HTML is never accepted.
BUILTINS = {
    'localizacao': ('Localização e endereço', '<path d="M20 10c0 6-8 11-8 11S4 16 4 10a8 8 0 1 1 16 0Z"/><circle cx="12" cy="10" r="3"/>'),
    'identificacao': ('Identificação e documento', '<rect x="3" y="5" width="18" height="14" rx="3"/><circle cx="8" cy="10" r="2"/><path d="M5 16c0-4 6-4 6 0M14 9h4M14 13h4"/>'),
    'usuario': ('Usuário e cadastro', '<circle cx="9" cy="7" r="4"/><path d="M2 21v-2a7 7 0 0 1 14 0v2M19 8v8M15 12h8"/>'),
    'carrinho': ('Carrinho e compras', '<path d="M2 3h3l3 13h11l3-10H6M9 20h.01M18 20h.01"/><circle cx="9" cy="20" r="1"/><circle cx="18" cy="20" r="1"/>'),
    'pacote': ('Pacote e armazenamento', '<path d="m3 7 9-4 9 4-9 4-9-4Zm0 0v10l9 4 9-4V7M12 11v10M7 5l9 4"/>'),
    'entrega': ('Entrega e transporte', '<path d="M2 6h12v12H2zM14 10h4l4 4v4h-8M17 10v5h5"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="19" r="2"/>'),
    'sino': ('Sino e aviso', '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4M12 2V1"/>'),
    'relogio': ('Relógio e prazo', '<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/>'),
    'calendario': ('Calendário', '<rect x="3" y="4" width="18" height="17" rx="3"/><path d="M7 2v5M17 2v5M3 10h18M7 14h2M14 14h3M7 18h2"/>'),
    'pagamento': ('Pagamento e dinheiro', '<rect x="2" y="5" width="20" height="14" rx="3"/><circle cx="12" cy="12" r="3"/><path d="M5 9h1M18 15h1"/>'),
    'seguranca': ('Segurança', '<path d="M12 2 3 6v6c0 5 9 10 9 10s9-5 9-10V6l-9-4Z"/><path d="m8 12 3 3 5-6"/>'),
    'duvidas': ('Dúvidas e atendimento', '<path d="M3 14v-2a9 9 0 0 1 18 0v2M21 17v1a3 3 0 0 1-3 3h-3"/><rect x="2" y="12" width="4" height="7" rx="2"/><rect x="18" y="12" width="4" height="7" rx="2"/><path d="M10 8a2 2 0 1 1 3 2c-1 1-1 1-1 2M12 15h.01"/>'),
}
ALIASES = {'endereco':'localizacao','documento':'identificacao','cadastro':'usuario','compras':'carrinho','armazenamento':'pacote','transporte':'entrega','aviso':'sino','prazo':'relogio','dinheiro':'pagamento','atendimento':'duvidas'}
MARKER = re.compile(r'^\[icone:([^\]\r\n]*)\][ \t]*(.*)$', re.I)
INLINE = re.compile(r'\*\*(.+?)\*\*|\[([^\]\n]+)\]\(([^\s)]+)\)|(https?://[^\s<>]+)')


def inline(text):
    out=[];cursor=0
    for match in INLINE.finditer(text):
        out.append(str(escape(text[cursor:match.start()])))
        if match[1] is not None:out.append('<strong>'+str(escape(match[1]))+'</strong>')
        else:
            label=match[2] or match[4];href=match[3] or match[4]
            suffix=''
            if match[4]:
                href=href.rstrip('.,;!');suffix=label[len(href):];label=href
            try:parsed=urlsplit(href)
            except ValueError:parsed=None
            allowed=parsed is not None and not any(ord(c)<32 for c in href) and (parsed.scheme.lower() in ('http','https','mailto') or (not parsed.scheme and (href.startswith('#') or href.startswith('/') and not href.startswith('//'))))
            if allowed:out.append('<a href="'+str(escape(href))+'">'+str(escape(label))+'</a>'+str(escape(suffix)))
            else:out.append(str(escape(match[0])))
        cursor=match.end()
    out.append(str(escape(text[cursor:])))
    return ''.join(out)


def blocks(lines):
    out=[];paragraph=[];items=[];kind=None;quote=[]
    def flush_paragraph():
        if paragraph:out.append('<p>'+'<br>'.join(inline(line) for line in paragraph)+'</p>');paragraph.clear()
    def flush_list():
        nonlocal kind
        if items:out.append('<'+kind+'>'+''.join('<li'+(f' value="{number}"' if number else '')+'>'+inline(text)+'</li>' for number,text in items)+'</'+kind+'>');items.clear()
        kind=None
    def flush_quote():
        if quote:out.append('<blockquote>'+'<br>'.join(inline(line) for line in quote)+'</blockquote>');quote.clear()
    for raw in lines:
        line=raw.strip();ordered=re.match(r'^(\d{1,9})\.\s+(.+)$',line)
        if not line:flush_paragraph();flush_list();flush_quote()
        elif line.startswith(('- ','* ')) or ordered:
            flush_paragraph();flush_quote();new_kind='ol' if ordered else 'ul'
            if kind and kind!=new_kind:flush_list()
            kind=new_kind;items.append((str(int(ordered[1])) if ordered else None,ordered[2] if ordered else line[2:]))
        elif line.startswith('> '):flush_paragraph();flush_list();quote.append(line[2:])
        else:flush_list();flush_quote();paragraph.append(line)
    flush_paragraph();flush_list();flush_quote()
    return ''.join(out)


def render_article(value,icons):
    """Input uses the historical escaped database representation, decoded once."""
    text=html.unescape(value or '')
    intro=[];sections=[];warnings=[];current=None;has_h2=False
    for number,line in enumerate(text.splitlines(),1):
        match=MARKER.match(line);heading=re.match(r'^(#{2,3})\s+(.+)$',line)
        if match or heading:
            name=match[1].strip().lower() if match else None
            title=match[2] if match else heading[2]
            if match and name not in icons:warnings.append(f'Linha {number}: ícone “{name}” não encontrado. Escolha outro identificador.')
            if not title.strip():warnings.append(f'Linha {number}: informe o texto do subtítulo.');continue
            level=2 if match or len(heading[1])==2 or not has_h2 else 3
            if level==2:has_h2=True
            current={'id':f'blog-secao-{len(sections)+1}','title':title,'title_html':inline(title),'level':level,'icon':icons.get(name) if name else None,'lines':[]}
            sections.append(current)
        else:(current['lines'] if current else intro).append(line)
    for section in sections:section['html']=blocks(section.pop('lines'))
    return {'intro':blocks(intro),'sections':sections,'warnings':warnings}
