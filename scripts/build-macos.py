#!/usr/bin/env python3
"""Build an isolated, self-contained arm64 technical preview. Never publishes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / '.build'
DIST = ROOT / 'dist'
VERSION = '0.2.0-beta.1'


def run(args, **kwargs):
    subprocess.run([str(a) for a in args], cwd=ROOT, check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--python', default=str(BUILD/'packaging-py312/bin/python'), help='Python with PyInstaller 6.22.2 installed')
    parser.add_argument('--output', default=str(DIST), help='Output directory for the app and ZIP')
    parser.add_argument('--sign', help='Developer ID Application identity; omit for an ad-hoc technical preview')
    args = parser.parse_args()
    # Prefer a matched compiler/SDK from Xcode without changing xcode-select.
    developer = Path('/Applications/Xcode.app/Contents/Developer')
    if developer.is_dir(): os.environ.setdefault('DEVELOPER_DIR', str(developer))
    identity = args.sign or '-'
    if args.sign and not args.sign.startswith('Developer ID Application:'):
        parser.error('--sign must be a Developer ID Application identity, not an Apple Development identity')
    output = Path(args.output).resolve()
    BUILD.mkdir(exist_ok=True); output.mkdir(parents=True, exist_ok=True)
    app = output/'ARGODRIVE.app'
    # Only replace this script's named app bundle, never arbitrary project files.
    if app.exists(): shutil.rmtree(app)
    resources = app/'Contents/Resources'; macos = app/'Contents/MacOS'
    resources.mkdir(parents=True); macos.mkdir()
    run(['xcrun','swiftc','-O','-swift-version','5','-target','arm64-apple-macosx14.0','-module-cache-path',BUILD/'swift-cache-xcode',
         '-framework','AppKit','-framework','WebKit',ROOT/'macos/ARGODRIVE.swift','-o',macos/'ARGODRIVE'])
    run(['xcrun','clang','-O2','-arch','arm64','-mmacosx-version-min=14.0','-o',BUILD/'k3-diskscope',ROOT/'monitor/k3-diskscope.c','-framework','IOKit','-framework','CoreFoundation'])
    freeze = [args.python,'-m','PyInstaller','--noconfirm','--clean','--onedir','--name','argodrive-server',
              '--target-arch','arm64','--distpath',BUILD/'frozen','--workpath',BUILD/'freeze-work','--specpath',BUILD,
              '--paths',ROOT/'monitor','--add-binary',str(BUILD/'k3-diskscope')+':.', '--log-level','WARN']
    for name in ['k3-live-page.html','app.css','app.js','app-model.js']:
        freeze += ['--add-data',str(ROOT/'monitor'/name)+':.']
    if args.sign: freeze += ['--codesign-identity',args.sign]
    freeze += [ROOT/'monitor/k3-live.py']
    run(freeze, env={**os.environ,'PYINSTALLER_CONFIG_DIR':str(BUILD/'pyinstaller-cache')})
    shutil.copytree(BUILD/'frozen/argodrive-server',resources/'backend',symlinks=True)
    shutil.copy2(ROOT/'macos/Info.plist',app/'Contents/Info.plist')
    run(['xcrun','swiftc','-swift-version','5','-module-cache-path',BUILD/'swift-cache-xcode',ROOT/'macos/MakeIcon.swift','-o',BUILD/'make-icon'])
    run([BUILD/'make-icon',BUILD/'AppIcon.iconset'])
    # Package the native vector renderings as PNG-backed ICNS representations.
    chunks = []
    for code, name in [('icp4','16x16'),('ic11','16x16@2x'),('icp5','32x32'),('ic12','32x32@2x'),('ic07','128x128'),('ic13','128x128@2x'),('ic08','256x256'),('ic14','256x256@2x'),('ic09','512x512'),('ic10','512x512@2x')]:
        data = (BUILD/'AppIcon.iconset'/('icon_'+name+'.png')).read_bytes()
        chunks.append(code.encode()+struct.pack('>I',len(data)+8)+data)
    data = b''.join(chunks)
    (resources/'AppIcon.icns').write_bytes(b'icns'+struct.pack('>I',len(data)+8)+data)
    shutil.copy2(ROOT/'LICENSE',resources/'LICENSE.txt')
    shutil.copy2(ROOT/'docs/BETA-TESTING.md',resources/'BETA-TESTING.md')
    # Python's license includes notices for bundled third-party code.
    license_path = Path(subprocess.check_output([args.python,'-c',"import sys,pathlib;p=pathlib.Path(sys.base_prefix);c=[p/'Resources/English.lproj/License.rtf',p/f'lib/python{sys.version_info.major}.{sys.version_info.minor}/LICENSE.txt'];print(next(x for x in c if x.exists()))"],text=True).strip())
    shutil.copy2(license_path,resources/('Python-License'+license_path.suffix))
    pyinstaller_license = subprocess.check_output([args.python,'-c',"import importlib.metadata as m;d=m.distribution('pyinstaller');print(next(d.locate_file(f) for f in d.files if str(f).endswith('/COPYING.txt')))"],text=True).strip()
    shutil.copy2(pyinstaller_license, resources/'PyInstaller-COPYING.txt')
    import_meta = subprocess.check_output([args.python,'-c',"import importlib.metadata as m,json;print(json.dumps({n:m.version(n) for n in ['pyinstaller','pyinstaller-hooks-contrib','macholib','altgraph','packaging']}))"],text=True)
    manifest = {'version':VERSION,'architecture':'arm64','minimum_macos':'14.0','signing':'developer-id' if args.sign else 'ad-hoc','notarized':False,
                'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                'source_dirty':bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip()),
                'build_dependencies':json.loads(import_meta),'python':subprocess.check_output([args.python,'--version'],text=True).strip()}
    (resources/'build-info.json').write_text(json.dumps(manifest,indent=2)+'\n')
    # PyInstaller signs its Python libraries. Sign the native executable and outer bundle last.
    sign = ['codesign','--force','--sign',identity]
    if args.sign: sign += ['--options','runtime','--timestamp']
    run(sign+[macos/'ARGODRIVE'])
    run(sign+[app])
    run(['codesign','--verify','--deep','--strict','--verbose=2',app])
    archive = output/f'ARGODRIVE-{VERSION}-macos-arm64.zip'
    if archive.exists(): archive.unlink()
    run(['ditto','-c','-k','--sequesterRsrc','--keepParent',app,archive])
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    (output/(archive.name+'.sha256')).write_text(digest+'  '+archive.name+'\n')
    print(json.dumps({'app':str(app),'archive':str(archive),'megabytes':round(archive.stat().st_size/1e6,1),'sha256':digest,'notarized':False},indent=2))

if __name__=='__main__':main()
