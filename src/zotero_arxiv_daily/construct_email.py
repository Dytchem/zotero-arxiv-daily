from .protocol import Paper

framework = """
<!DOCTYPE HTML>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background-color:#f4f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;">
<div style="max-width:680px;margin:0 auto;padding:24px 16px;">
  <div style="background:#ffffff;border-radius:14px;box-shadow:0 2px 12px rgba(0,0,0,0.06);overflow:hidden;">
    <div style="background:linear-gradient(135deg,#2563eb,#7c3aed);padding:22px 28px;">
      <div style="color:#ffffff;font-size:22px;font-weight:700;">Daily arXiv</div>
      <div style="color:rgba(255,255,255,0.85);font-size:13px;margin-top:4px;">Tailored to your Zotero library</div>
    </div>
    <div style="padding:24px 28px;">
      __CONTENT__
    </div>
  </div>
  <div style="text-align:center;color:#9ca3af;font-size:12px;padding:16px 0 8px;">
    To unsubscribe, remove your email in your Github Action setting.
  </div>
</div>
</body>
</html>
"""


def get_empty_html():
    return """
    <div style="text-align:center;padding:40px 20px;">
      <div style="font-size:20px;font-weight:700;color:#111827;">No Papers Today. Take a Rest!</div>
      <div style="font-size:14px;color:#6b7280;margin-top:8px;">Your Zotero library had no new matching papers today.</div>
    </div>
    """


def get_block_html(title:str, authors:str, rate:str, tldr:str, pdf_url:str, affiliations:str | None=None):
    block_template = """
    <div style="border:1px solid #e5e7eb;border-radius:10px;padding:18px 20px;margin-bottom:16px;background:#ffffff;">
      <div style="font-size:17px;font-weight:700;color:#111827;line-height:1.4;">{title}</div>
      <div style="font-size:13px;color:#6b7280;margin-top:8px;line-height:1.5;">
        {authors}
        <div style="margin-top:2px;font-style:italic;">{affiliations}</div>
      </div>
      <div style="margin-top:10px;">
        <span style="display:inline-block;background:#eef2ff;color:#4f46e5;font-size:12px;font-weight:600;padding:3px 10px;border-radius:999px;">Relevance: {rate}</span>
      </div>
      <div style="margin-top:12px;padding:10px 14px;border-left:4px solid #2563eb;background:#f8fafc;border-radius:6px;font-size:14px;color:#374151;line-height:1.55;">
        <strong>TLDR:</strong> {tldr}
      </div>
      <div style="margin-top:14px;">
        <a href="{pdf_url}" style="display:inline-block;text-decoration:none;font-size:13px;font-weight:700;color:#ffffff;background:linear-gradient(135deg,#2563eb,#4f46e5);padding:9px 18px;border-radius:8px;">PDF</a>
      </div>
    </div>
    """
    return block_template.format(title=title, authors=authors,rate=rate, tldr=tldr, pdf_url=pdf_url, affiliations=affiliations)


def render_email(papers:list[Paper]) -> str:
    parts = []
    if len(papers) == 0 :
        return framework.replace('__CONTENT__', get_empty_html())

    for p in papers:
        rate = round(p.score, 1) if p.score is not None else 'Unknown'
        author_list = list(p.authors)
        num_authors = len(author_list)
        if num_authors <= 5:
            authors = ', '.join(author_list)
        else:
            authors = ', '.join([*author_list[:3], '...', *author_list[-2:]])
        if p.affiliations is not None:
            affiliations = p.affiliations[:5]
            affiliations = ', '.join(affiliations)
            if len(p.affiliations) > 5:
                affiliations += ', ...'
        else:
            affiliations = 'Unknown Affiliation'
        parts.append(get_block_html(p.title, authors, rate, p.tldr, p.pdf_url, affiliations))

    content = ''.join(parts)  # cards carry their own spacing
    return framework.replace('__CONTENT__', content)
