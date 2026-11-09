# Concordance archive format

The canonical file has this shape:

```json
{
  "schema_version": 1,
  "metadata": {
    "kind": "direct_message",
    "title": "Conversation title",
    "channel_id": "...",
    "display_timezone": "America/Phoenix",
    "source": {"type": "user_supplied"}
  },
  "participants": [
    {"id": "user-1", "display_name": "User 1", "avatar_path": "assets/avatars/user-1.svg"}
  ],
  "messages": [
    {
      "id": "message-1",
      "author_id": "user-1",
      "timestamp": "2018-11-30T06:52:00Z",
      "content": "Good one"
    }
  ]
}
```

See `src/concordance/core.py` for the validator, and `fixtures/sample/archive.json` for a complete synthetic example.
