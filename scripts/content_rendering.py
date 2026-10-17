"""Keep public attribution visible without including it in section copies."""
import html
import re
from urllib.parse import urlparse

SOURCE_LABEL = re.compile(r'^\s*(?:자료|공식\s*출처|출처)\s*:')
SOURCE_META = re.compile(r'^\s*(?:원자료|편집\s*검토|공식\s*본문\s*연결\s*확인)\s*:')


def split_content_sources(text):
    body, sources = [], []
    in_source = False
    for line in text.splitlines():
        stripped = line.strip()
        if SOURCE_LABEL.match(line) or SOURCE_META.match(line):
            sources.append(stripped)
            in_source = True
        elif in_source and re.match(r'^https?://\S+$', stripped):
            sources.append(stripped)
        else:
            body.append(line)
            in_source = False
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(body)).strip(), '\n'.join(sources)


def render_sources(text):
    """Only plain source text and normal web links; never source-provided HTML."""
    pieces = re.split(r'(https?://[^\s<>]+)', text)
    return ''.join(
        '<a href="' + html.escape(piece, quote=True)
        + '" target="_blank" rel="noopener noreferrer">'
        + html.escape(piece) + '</a>'
        if re.match(r'^https?://', piece) else html.escape(piece)
        for piece in pieces
    )


def render_notice_links(text):
    blocks = []
    for block in text.split('\n\n'):
        lines = block.splitlines()
        url = lines[-1] if len(lines) >= 2 else ''
        parsed = urlparse(url)
        allowed = ((parsed.hostname == 'www.myhome.go.kr'
                    and parsed.path.endswith('/selectRsdtRcritNtcDetailView.do'))
                   or (parsed.hostname == 'apply.lh.or.kr'
                       and parsed.path.endswith('/selectWrtancInfo.do'))
                   or (parsed.hostname == 'www.eum.go.kr'
                       and parsed.path == '/web/gs/gv/gvGosiDet.jsp')
                   or (parsed.hostname == 'www.molit.go.kr'
                       and parsed.path == '/USR/BORD0201/m_69/DTL.jsp'))
        if parsed.scheme == 'https' and allowed:
            blocks.append('<a href="' + html.escape(url, quote=True)
                          + '" target="_blank" rel="noopener noreferrer">'
                          + html.escape(lines[0]) + '</a>'
                          + ('\n' + html.escape('\n'.join(lines[1:-1])) if len(lines) > 2 else ''))
        else:
            blocks.append(html.escape(block))
    return '\n\n'.join(blocks)
