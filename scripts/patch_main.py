with open('sim/__main__.py', 'r', encoding='utf-8') as f:
    c = f.read()
c = c.replace('Path(a.out).write_text(out)', 'Path(a.out).write_text(out, encoding="utf-8")')
with open('sim/__main__.py', 'w', encoding='utf-8') as f:
    f.write(c)
print("Updated sim/__main__.py successfully")
