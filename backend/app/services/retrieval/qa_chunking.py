"""QA-only, model-token counted, structure-aware parent/child segmentation.

Offsets always address the saved paper_documents.text, not reconstructed prose.
Atomic tables, display math and captions may exceed the target size, but never
the embedding limit. Oversized indivisible objects fail safely instead of truncating.
"""
import gzip
import hashlib
import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

VERSION = 'parent-child-tokens-v3'
TOKENIZER_SHA256 = '21106b6d7dab2952c1d496fb21d5dc9db75c28ed361a05f5020bbba27810dd08'


@lru_cache(maxsize=4)
def _tokenizer(path):
    from tokenizers import Tokenizer
    p = Path(path)
    blob = gzip.decompress(p.read_bytes()) if p.suffix == '.gz' else p.read_bytes()
    if p.name == 'bge-m3.json.gz' and hashlib.sha256(blob).hexdigest() != TOKENIZER_SHA256:
        raise ValueError('BGE tokenizer resource checksum mismatch')
    t = Tokenizer.from_str(blob.decode('utf-8'))
    t.no_truncation(); t.no_padding()
    return t


def tokenizer():
    from app.infrastructure import embeddings
    override = os.getenv('QA_TOKENIZER_PATH', '').strip()
    if not override and embeddings.model_name().lower() != 'baai/bge-m3':
        raise ValueError('Changing QA embedding models requires QA_TOKENIZER_PATH and QA_EMBED_MAX_TOKENS')
    return _tokenizer(override or str(Path(__file__).resolve().parents[2] / 'assets/tokenizers/bge-m3.json.gz'))


def count(text):
    return len(tokenizer().encode(text, add_special_tokens=False).ids)


def signature():
    """Queue revision identity does not require a working tokenizer/model setup."""
    path = os.getenv('QA_TOKENIZER_PATH', '').strip()
    p=Path(path) if path else None
    digest = _resource_digest(path,p.stat().st_mtime_ns,p.stat().st_size) if p and p.is_file() else TOKENIZER_SHA256 if not path else path
    settings = [os.getenv(k, v) for k,v in (
        ('QA_CHILD_TOKENS','350'),('QA_CHILD_OVERLAP_TOKENS','50'),
        ('QA_PARENT_TOKENS','1000'),('QA_EMBED_MAX_TOKENS','8192'))]
    return hashlib.sha256(json.dumps([VERSION,digest,*settings]).encode()).hexdigest()


@lru_cache(maxsize=8)
def _resource_digest(path, modified, size):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def integer(name, default):
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(name + ' must be positive')
    return value


@dataclass(frozen=True)
class Config:
    child: int
    overlap: int
    parent: int
    embedding_max: int
    tokenizer_hash: str

    @classmethod
    def configured(cls):
        from app.infrastructure import embeddings
        t = tokenizer()
        if embeddings.model_name().lower() != 'baai/bge-m3' and not os.getenv('QA_EMBED_MAX_TOKENS'):
            raise ValueError('Unknown embedding model requires QA_EMBED_MAX_TOKENS')
        maximum = integer('QA_EMBED_MAX_TOKENS', 8192)
        if embeddings.model_name().lower() == 'baai/bge-m3':
            maximum = min(maximum, 8192)
        available = maximum - t.num_special_tokens_to_add(False)
        child = min(integer('QA_CHILD_TOKENS', 350), available)
        parent = min(integer('QA_PARENT_TOKENS', 1000), available)
        overlap = int(os.getenv('QA_CHILD_OVERLAP_TOKENS', '50'))
        if available < 16 or child > parent or not 0 <= overlap < child:
            raise ValueError('Invalid QA parent/child token budgets')
        # Includes custom vocabulary changes in durable revisions.
        digest = signature()
        return cls(child, overlap, parent, available, digest)

    def signature(self):
        return [VERSION, self.child, self.overlap, self.parent, self.embedding_max, self.tokenizer_hash]


@dataclass
class Unit:
    start: int
    end: int
    path: tuple
    block: str
    page: int | None
    atomic: bool = False


def units(text, structure):
    """Prefer MinerU block/page provenance; Markdown is a structural fallback."""
    sections = {s.get('id'): s for s in (structure or {}).get('sections', [])}
    result = []; cursor = 0; heading = ('正文',)
    for i, block in enumerate((structure or {}).get('blocks') or []):
        body = (block.get('text') or '').strip()
        if not body:
            continue
        start = text.find(body, cursor)
        if start < 0:
            # Do not invent offsets when persisted structure differs from text.
            return markdown_units(text)
        cursor = start + len(body)
        lineage = []; sid = block.get('section_id'); seen = set()
        while sid in sections and sid not in seen:
            seen.add(sid); s = sections[sid]; lineage.insert(0, s.get('title') or '正文'); sid = s.get('parent_id')
        if block.get('heading_level'):
            heading = tuple(lineage or [body])
        path = tuple(lineage) or heading
        page = block.get('page_number')
        result.append(Unit(start, cursor, path, block.get('id') or f'b{i+1}',
                           page if type(page) is int and page > 0 else None,
                           block.get('type') in ('table', 'image', 'equation', 'formula') or atomic(body)))
    return result or markdown_units(text)


def atomic(body):
    return bool(re.search(r'<table\b|^\s*\|.+\||^\s*\$\$|^\s*\\\[|^\s*```|^\s*!\[', body, re.I | re.M))


def markdown_units(text):
    lines = list(re.finditer(r'.*(?:\n|$)', text)); result = []; stack = []; i = 0
    while i < len(lines):
        line = lines[i]; body = line.group().strip()
        if not body:
            i += 1; continue
        heading = re.match(r'^(#{1,6})\s+(.+)', body)
        if heading:
            depth = len(heading[1])
            while stack and stack[-1][0] >= depth:stack.pop()
            stack.append((depth, heading[2].strip()))
        path = tuple(s[1] for s in stack) or ('正文',)
        start = line.start(); end = line.end(); j = i + 1
        mode = ('table' if '<table' in body.lower() else 'math' if body.startswith('$$') else
                'latex' if body.startswith('\\[') else 'code' if body.startswith('```') else
                'pipe' if body.startswith('|') else None)
        closed = mode == 'table' and '</table>' in body.lower() or mode == 'math' and body.count('$$') >= 2 or mode == 'latex' and '\\]' in body
        if mode and not closed:
            while j < len(lines):
                value = lines[j].group().strip()
                if mode == 'pipe' and not value.startswith('|'):break
                end = lines[j].end(); j += 1
                if mode == 'table' and '</table>' in value.lower() or mode == 'math' and '$$' in value or mode == 'latex' and '\\]' in value or mode == 'code' and value.startswith('```'):break
        elif not mode and not heading:
            while j < len(lines):
                value = lines[j].group().strip()
                if not value or re.match(r'^(#{1,6}\s|\$\$|\\\[|```|<table|\|)', value, re.I):break
                end = lines[j].end(); j += 1
        while end > start and text[end-1].isspace():end -= 1
        result.append(Unit(start, end, path, f'md{len(result)+1}', None, bool(mode) or atomic(text[start:end])))
        i = max(i+1, j)
    # Caption and its object travel together, including exact intervening whitespace.
    joined = []
    for u in result:
        if u.atomic and joined and joined[-1].path == u.path and re.match(r'^(?:Table|Figure|Fig\.|表|图)\s*\d', text[joined[-1].start:joined[-1].end], re.I):
            previous = joined.pop(); u = Unit(previous.start, u.end, u.path, previous.block+'+'+u.block, None, True)
        joined.append(u)
    return joined


def pieces(text, unit, limit):
    body = text[unit.start:unit.end]
    if unit.atomic or count(body) <= limit:
        return [(unit.start, unit.end)]
    # Sentences precede token-boundary fallback for exceptionally long sentences.
    ends = [m.end() for m in re.finditer(r'[。！？!?;；]|[.](?=\s|$)|\n\s*\n', body)] + [len(body)]
    result = []; start = 0
    for end in sorted(set(ends)):
        while count(body[start:end]) > limit:
            offsets = tokenizer().encode(body[start:end], add_special_tokens=False).offsets
            cut = start + offsets[limit-1][1]
            if cut <= start:raise ValueError('Tokenizer returned invalid offsets')
            result.append((unit.start+start, unit.start+cut)); start = cut
        if end > start:
            result.append((unit.start+start, unit.start+end)); start = end
    return result


def build(text, structure, paper_id, local_id, revision, config=None):
    config = config or Config.configured()
    source = units(text, structure); groups = []; group = []
    for u in source:
        if u.atomic and count(text[u.start:u.end]) > config.embedding_max:
            raise ValueError('Table/formula exceeds embedding limit; original retained, no silent truncation')
        for start, end in pieces(text, u, config.parent):
            part = Unit(start, end, u.path, u.block, u.page, u.atomic)
            if group and (group[0].path != part.path or count(text[group[0].start:end]) > config.parent):
                groups.append(group); group = []
            group.append(part)
    if group:groups.append(group)
    parents = []; children = []
    for group in groups:
        start, end = group[0].start, group[-1].end
        parent_id = f'QP{len(parents)+1:05d}:{paper_id}:{revision[:12]}'
        def metadata(a, b):
            covered = [u for u in group if u.start < b and u.end > a]
            pages = [u.page for u in covered if u.page is not None]
            return {'paper_id':paper_id,'local_id':local_id,'section':group[0].path[-1],
                    'section_path':list(group[0].path),'paragraph_ids':list(dict.fromkeys(u.block for u in covered)),
                    'page_start':min(pages) if pages else None,'page_end':max(pages) if pages else None,
                    'source_start':a,'source_end':b,'start_char':a,'source_hash':revision,'basis':'full_text'}
        parents.append({**metadata(start,end),'parent_id':parent_id,'text':text[start:end],
                        'token_count':count(text[start:end])})
        parts = [p for u in group for p in pieces(text,u,config.child)]
        windows = []; a = None; b = None
        for left, right in parts:
            if a is not None and count(text[a:right]) > config.child:
                windows.append((a,b))
                # Only overlap ordinary prose. Never copy a fragment of a table/formula.
                tail = tokenizer().encode(text[a:b],add_special_tokens=False).offsets
                overlap_start = a + tail[-config.overlap][0] if config.overlap and len(tail) > config.overlap else b
                crossing_atomic = any(u.atomic and u.start < b and u.end > overlap_start for u in group)
                a = overlap_start if not crossing_atomic and count(text[overlap_start:right]) <= config.child else left
            if a is None:a = left
            b = right
        if a is not None:windows.append((a,b))
        for a,b in windows:
            body = text[a:b]
            if count(body) > config.embedding_max:raise ValueError('QA child exceeds embedding input limit')
            children.append({**metadata(a,b),'parent_id':parent_id,'chunk_id':f'QC{len(children)+1:05d}:{paper_id}:{revision[:12]}',
                             'chunk_index':len(children)+1,'text':body,'token_count':count(body),
                             'parent':parents[-1]})
    return children
