"""Acceptance checks that need neither Discord credentials nor paid model calls."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import docx
import openpyxl
import pptx
from pypdf import PdfReader
from reportlab.pdfgen import canvas

target=Path('/workspace/deployment-checks')
target.mkdir(exist_ok=True)
marker='Jetson Bot document verification'
word=docx.Document()
word.add_paragraph(marker)
word.save(target/'sample.docx')
assert docx.Document(target/'sample.docx').paragraphs[0].text==marker
book=openpyxl.Workbook()
book.active['A1']=marker
book.active['B1']=42
book.save(target/'sample.xlsx')
assert openpyxl.load_workbook(target/'sample.xlsx').active['B1'].value==42
slides=pptx.Presentation()
slide=slides.slides.add_slide(slides.slide_layouts[0])
slide.shapes.title.text=marker
slides.save(target/'sample.pptx')
assert pptx.Presentation(target/'sample.pptx').slides[0].shapes.title.text==marker
pdf=canvas.Canvas(str(target/'sample.pdf'))
pdf.drawString(72,720,marker)
pdf.save()
assert marker in PdfReader(target/'sample.pdf').pages[0].extract_text()
print('PASS: Word, Excel, PowerPoint and PDF creation/readback')

spec=importlib.util.spec_from_file_location('jetson_controls','/opt/data/plugins/jetson-controls/__init__.py')
plugin=importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)
result=plugin.handle('budget')
assert 'Daily allowance: $20.00' in result,result
print('PASS: non-model budget control')

host=subprocess.run(['ssh','jetson-host','sudo','-n','id','-u'],
                    check=True,capture_output=True,text=True,timeout=20)
assert host.stdout.strip()=='0'
print('PASS: pinned-key SSH host administration')

memory=Path('/opt/data/memories')
memory.mkdir(exist_ok=True)
(memory/'deployment-check.txt').write_text(marker+'\n')
skill=Path('/opt/data/skills/jetson-deployment-check')
skill.mkdir(exist_ok=True)
(skill/'SKILL.md').write_text('---\nname: jetson-deployment-check\ndescription: Verify this Jetson installation without paid calls.\n---\n\nRun `sudo jetsonctl status` through `ssh jetson-host`.\n')
print('PASS: persistent memory and reusable skill written')
import resource
print('Peak check-process memory: %.1f MiB' % (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024))
