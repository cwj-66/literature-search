"""Run on the existing production host after uploading the explicit release archive.

No credential is read or copied. Existing server .env files remain in place.
"""
from datetime import datetime, timezone
from pathlib import Path
import json
import shutil
import subprocess
import tarfile
import time
import urllib.request

ROOT = Path('/opt/paper-search')
stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
backup = ROOT / 'backups' / ('portfolio-demo-' + stamp)
backup.mkdir(parents=True)


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


image = subprocess.check_output(['docker', 'inspect', 'paper-search-api-1', '--format', '{{.Image}}'], text=True).strip()
run(['docker', 'tag', image, 'paper-search-demo-rollback:' + stamp])
for source, dest in [('/etc/nginx/sites-available/game-support-demo', 'portfolio-nginx.conf'),
                     ('/etc/nginx/sites-available/paper-search', 'paper-nginx.conf'),
                     ('/opt/chenwj-portfolio/public/paper-search.html', 'paper-search.html')]:
    shutil.copy2(source, backup / dest)
with tarfile.open(backup / 'previous-code.tar.gz', 'w:gz') as archive:
    for path in ('backend/app', 'deploy', 'frontend/dist'):
        archive.add(ROOT / path, arcname=path)
snapshot = '/app/data/pre-demo-' + stamp + '.sqlite3'
run(['docker', 'exec', 'paper-search-api-1', 'python', '-c',
     "import sqlite3; a=sqlite3.connect('/app/data/papers.sqlite3'); b=sqlite3.connect(" + repr(snapshot) + "); a.backup(b); a.close(); b.close()"])
print('BACKUP', backup, flush=True)

archive_path = ROOT / 'incoming-portfolio-demo.tar.gz'
staging = ROOT / ('release-demo-' + stamp)
staging.mkdir()
with tarfile.open(archive_path) as archive:
    for member in archive.getmembers():
        assert (staging / member.name).resolve().is_relative_to(staging.resolve())
        assert member.isfile() or member.isdir(), member.name
    archive.extractall(staging)

# Exercise the release's actual code using the already-installed production runtime.
run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'python', '-e', 'PYTHONPATH=/app',
     '-v', str(staging / 'backend/app') + ':/app/app:ro',
     '-v', str(staging / 'backend/tests') + ':/tests:ro',
     image, '/tests/run_offline.py', 'test_portfolio_demo', 'test_agent_direct', 'test_conversations', 'test_research_tools'])

run(['docker', 'exec', '-i', '-e', 'SQLITE_TMPDIR=/app/data', 'paper-search-api-1', 'python', '-'],
    input=(staging / 'deploy/prepare_demo_seed.py').read_text(), text=True)

for folder in ('backend/app', 'deploy', 'frontend/dist'):
    source = staging / folder
    target = ROOT / folder
    # Remove only the previous frontend build after its complete backup. Backend
    # sources are overlaid so unrelated deployment files remain available.
    if folder == 'frontend/dist':
        shutil.rmtree(target)
    shutil.copytree(source, target, dirs_exist_ok=True)

compose = ['docker', 'compose', '-p', 'paper-search', '-f', 'deploy/compose.yml', '-f', 'deploy/compose.demo.yml']
run(compose + ['build', 'api'], cwd=ROOT)
run(compose + ['up', '-d', '--no-deps', 'api'], cwd=ROOT)
for _ in range(40):
    try:
        with urllib.request.urlopen('http://127.0.0.1:15179/api/health', timeout=3) as response:
            if response.status == 200:
                break
    except Exception:
        time.sleep(1)
else:
    raise RuntimeError('API health did not recover; Nginx and portfolio entry were not changed')

snippet = Path('/etc/nginx/snippets/portfolio-paper-demo.conf')
shutil.copy2(ROOT / 'deploy/nginx-portfolio-paper.conf', snippet)
portfolio_nginx = Path('/etc/nginx/sites-available/game-support-demo')
text = portfolio_nginx.read_text()
marker = '    location = /_portfolio_auth {'
assert marker in text
text = text.replace(marker, '    include /etc/nginx/snippets/portfolio-paper-demo.conf;\n\n' + marker, 1)
portfolio_nginx.write_text(text)
paper_nginx = Path('/etc/nginx/sites-available/paper-search')
# The standalone papers domain is managed independently; never redirect it to the portfolio demo.

try:
    run(['nginx', '-t'])
except Exception:
    shutil.copy2(backup / 'portfolio-nginx.conf', portfolio_nginx)
    shutil.copy2(backup / 'paper-nginx.conf', paper_nginx)
    raise
run(['systemctl', 'reload', 'nginx'])

page = Path('/opt/chenwj-portfolio/public/paper-search.html')
text = page.read_text()
# Scope the replacement to the two experience anchors, preserving unrelated links.
import re
def replace_link(match):
    anchor = match.group(0)
    anchor = anchor.replace('https://papers.chenwj.click/', '/demo/paper-search/').replace('https://papers.chenwj.click', '/demo/paper-search/')
    return re.sub(r'\s+target=["\']_blank["\']', '', anchor)
text, count = re.subn(r'<a\b[^>]*href=["\']https://papers\.chenwj\.click/?["\'][^>]*>', replace_link, text)
assert count == 2, count
page.write_text(text)
print('RELEASE_OK', stamp, 'BACKUP', backup, flush=True)
