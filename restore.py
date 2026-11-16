import json

full_lines = open(r'C:\Users\ADMIN\.gemini\antigravity\brain\c935662d-bdd1-4dab-b03e-051b21bad624\.system_generated\logs\transcript_full.jsonl', 'r', encoding='utf-8').readlines()
e4504 = json.loads(full_lines[4504])['tool_calls'][0]['args']
e4496 = json.loads(full_lines[4496])['tool_calls'][0]['args']
edits_4449 = [json.loads(full_lines[i])['tool_calls'][0]['args'] for i in [4449, 4451, 4453, 4455, 4459]]

cur = open(r'C:\Users\ADMIN\faceid-auth-desktop\gui\web\index.html', 'r', encoding='utf-8').read()
content = cur
content = content.replace(e4504['Replacement'], e4504['Target'])
content = content.replace(e4496['Replacement'], e4496['Target'])
content = content.replace(edits_4449[1]['Replacement'], edits_4449[1]['Target'])
content = content.replace(edits_4449[0]['Replacement'], edits_4449[0]['Target'])
content = content.replace(edits_4449[4]['Replacement'], edits_4449[4]['Target'])
content = content.replace(edits_4449[2]['Replacement'], edits_4449[2]['Target'])

old_mid = 'document.getElementById(\'barFused\').style.width = fused + "%";\n\n\n\n        const box = document.getElementById(\'telDecisionBox\');'
new_mid = 'document.getElementById(\'barFused\').style.width = fused + "%";\n\n' + edits_4449[3]['Target'] + '\n\n        const box = document.getElementById(\'telDecisionBox\');'

print('old_mid in content:', old_mid in content)
content = content.replace(old_mid, new_mid)
open(r'C:\Users\ADMIN\faceid-auth-desktop\gui\web\index.html', 'w', encoding='utf-8').write(content)
print('Restoration completed! File length:', len(content))
