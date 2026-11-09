#!/usr/bin/python3
"""Generate first-install settings without storing secrets in source files."""
import json
import os
import secrets
from pathlib import Path

root=Path('/etc/jetson-bot')
state=Path('/srv/jetson-bot/state')
os.umask(0o077)
local_path=root/'local-token'
if not local_path.exists(): local_path.write_text(secrets.token_urlsafe(48)+'\n')
local=local_path.read_text().strip()
budget=dict(model='grok-4.7',upstream='https://api.x.ai/v1',
            input_ticks_per_token=20000,output_ticks_per_token=60000,
            max_input_tokens=191808,max_output_tokens=8192,daily_cap_ticks=200000000000,
            image_tokens=2000,max_images=4,max_image_bytes=1500000,
            timezone='America/Toronto',database='/budget/ledger.sqlite',
            token_file='/etc/jetson-bot/local-token',key_file='/etc/jetson-bot/xai-key')
if not (root/'budget.json').exists(): (root/'budget.json').write_text(json.dumps(budget,indent=2)+'\n')
aux={name:dict(provider='main',model='grok-4.7',extra_body={'max_completion_tokens':8192})
     for name in ('compression','goal_judge','skills_hub','mcp','review','curator','memory_query_rewrite',
                  'approval','title_generation','background_review','kanban_decomposer','triage_specifier')}
aux['title_generation']['enabled']=False
aux['background_review']['enabled']=False
for name in ('compression','goal_judge'):
    aux[name]['extra_body']={'max_completion_tokens':4096,'reasoning_effort':'low'}
aux['compression']['extra_body']['max_completion_tokens']=8192
config=dict(
    model=dict(provider='custom:jetson-budget',default='grok-4.7',
               base_url='http://jetson-budget:8870/v1',api_mode='chat_completions',context_length=64000,
               streaming=False),
    providers={'jetson-budget':dict(api='http://jetson-budget:8870/v1',key_env='JETSON_LOCAL_TOKEN',
                                   transport='chat_completions',models={'grok-4.7':dict(context_length=64000)})},
    timezone='America/Toronto',max_concurrent_sessions=1,max_live_sessions=2,
    agent=dict(max_turns=100,reasoning_effort='medium',agent_cache=dict(max_size=2,protect_recent=1)),
    goals=dict(max_turns=100),cron=dict(max_parallel_jobs=1),kanban=dict(max_in_progress=1),
    delegation=dict(max_concurrent_children=1,max_spawn_depth=1,subagent_auto_approve=True),
    compression=dict(enabled=True,progress_notices=False,threshold_tokens=32000,tail_mode='legacy',protect_first_n=1,
                     protect_last_n=4,target_ratio=0.10),auxiliary=aux,
    terminal=dict(backend='local',cwd='/workspace',home_mode='profile'),
    approvals=dict(mode='off'),plugins=dict(enabled=['jetson-controls'],
        entries={'jetson-controls':dict(allow_gateway_injection=True)}),
    gateway=dict(unauthorized_dm_behavior='ignore'),platforms=dict(api_server=dict(enabled=False)),
    streaming=dict(enabled=False),stt=dict(enabled=False),tts=dict(enabled=False),
    display=dict(tool_progress='off',interim_assistant_messages=False,show_reasoning=False,
                 show_commentary=False,memory_notifications='off',
                 long_running_notifications=False,busy_ack_detail=False,busy_steer_ack_enabled=False,
                 background_process_notifications='error',suppress_warning_notifications=True,
                 platforms={'discord':dict(tool_progress='off',interim_assistant_messages=False,
                                          long_running_notifications=False,busy_ack_detail=False,
                                          busy_steer_ack_enabled=False,
                                          show_reasoning=False,show_commentary=False,
                                          memory_notifications='off',streaming=False)}),
    web=dict(search_backend='ddgs',keyless_fallback=False,keyless_rescue=False),
    webapp=dict(enabled=False),discord=dict(free_response_auto_thread=True),
)
if not (state/'config.yaml').exists(): (state/'config.yaml').write_text(json.dumps(config,indent=2)+'\n')
if not (state/'runtime.env').exists():
    (state/'runtime.env').write_text('JETSON_LOCAL_TOKEN='+local+'\nAPI_SERVER_ENABLED=false\nHERMES_DASHBOARD=0\n')
if not (state/'.env').exists(): (state/'.env').write_text((state/'runtime.env').read_text())
