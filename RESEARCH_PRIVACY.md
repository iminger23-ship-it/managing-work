# Research + Screen Privacy

Internet is opt-in and limited to public HTTP/HTTPS hosts; localhost and private/link-local/loopback targets are blocked.

Screen learning is opt-in. Background observation stores foreground application/title timing, not raw screenshots. Common sensitive-looking windows are excluded. A screenshot is only captured by the explicit `screen snapshot` command while observation is enabled.

The learning flow is observe -> detect repeated transition -> create candidate -> user reviews/approves -> deterministic action router executes the approved workflow.
