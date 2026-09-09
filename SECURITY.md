# Security

JARVIS can inspect repositories, run approved commands, apply approved patches, and send approved email. Treat it as a privileged local application.

- Never commit `~/.config/jarvis/secrets.env` or copy credentials into this repository.
- Keep the default bind address at `127.0.0.1`.
- Use private Tailscale Serve for remote access. Do not use Funnel or an unauthenticated public tunnel.
- Review every action card before approving it.
- Phone uploads are stored under `~/Desktop/JARVIS_UPLOADS` with mode `600`; treat that inbox as private.
- Microphone recordings are transcribed locally and their temporary audio files are deleted after each request.
- Report vulnerabilities privately to the repository owner rather than opening an issue containing credentials.
