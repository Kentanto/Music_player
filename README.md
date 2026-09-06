1. Add this line to `/etc/sudoers` (run `sudo visudo`):

```javascript
livingroompi ALL=(ALL) NOPASSWD: /usr/bin/cec-ctl, /usr/bin/stdbuf
```

(Replace `livingroompi` with your username if different)
