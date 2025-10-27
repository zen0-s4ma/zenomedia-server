[unix_http_server]
file=/var/run/supervisor.sock

[supervisord]
nodaemon=true
logfile=/var/log/supervisord.log
pidfile=/var/run/supervisord.pid
childlogdir=/var/log

[rpcinterface:supervisor]
supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface

[supervisorctl]
serverurl=unix:///var/run/supervisor.sock

[inet_http_server]
port=0.0.0.0:9001
username=%(ENV_SUPERVISOR_USER)s
password=%(ENV_SUPERVISOR_PASSWORD)s

[program:cron]
command=/usr/sbin/cron -f
autostart=true
autorestart=true
stdout_logfile=/var/log/cron.stdout.log
stderr_logfile=/var/log/cron.stderr.log

[include]
files=/etc/supervisor/conf.d/*.ini
