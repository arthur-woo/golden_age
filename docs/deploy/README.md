# 상주 프로세스 배포 (안전#13)

`collect_realtime`(실시간 수집)과 `qcluster`(스케줄 실행)는 **장중 상주**해야 한다.
아래 systemd/supervisor 예시로 자동 재시작·부팅 상주를 구성한다.
`<ID>`는 계좌 id, 경로/venv는 환경에 맞게 수정.

## systemd (예시)

`/etc/systemd/system/golden-collect.service`
```ini
[Unit]
Description=Golden Age realtime collector
After=network.target postgresql.service

[Service]
Type=simple
WorkingDirectory=/opt/golden_age/backend
ExecStart=/opt/golden_age/.venv/bin/python manage.py collect_realtime --universe --top 40 --account-id 1
Restart=always
RestartSec=5
Environment=DJANGO_SETTINGS_MODULE=config.settings.local

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/golden-qcluster.service`
```ini
[Unit]
Description=Golden Age django_q cluster
After=network.target postgresql.service

[Service]
Type=simple
WorkingDirectory=/opt/golden_age/backend
ExecStart=/opt/golden_age/.venv/bin/python manage.py qcluster
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now golden-collect golden-qcluster
sudo systemctl status golden-collect
```

## 장 시간대 자동 기동/정지
`collect_realtime`는 장중(09:00~15:30 KST)에만 의미가 있다. systemd timer 또는 cron으로
09:00 start / 15:40 stop을 걸거나, 프로세스가 장외 시간엔 유휴하도록 운영한다.

```cron
0 9 * * 1-5   systemctl start golden-collect
40 15 * * 1-5 systemctl stop golden-collect
```

## 헬스체크 연동 (안전#12)
외부 감시(cron/systemd timer)가 주기적으로:
```bash
/opt/golden_age/.venv/bin/python manage.py health_check 1 || <알림 전송>
```
`health_check`는 데이터 신선도·미체결·마지막 실행 상태를 점검하고 이상 시 exit 1.
