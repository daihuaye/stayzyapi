# Stayzy sign-in email

Set the SendGrid template version's **Subject** field to `{{subject}}`.
The HTML title alone does not set the email subject.

Paste `magic-link.html` into the code editor for your SendGrid dynamic template,
then activate that version. Set `STAYZY_SENDGRID_MAGIC_LINK_TEMPLATE_ID` to its
template ID.

The API provides these dynamic template values:

- `{{subject}}`: the email subject, currently "Your sign-in link for Stayzy".
- `{{magic_link}}`: the sign-in URL, used by the button and fallback link.
- `{{expires_minutes}}`: the link lifetime, currently 15 minutes.

Preview data in SendGrid:

```json
{
  "subject": "Your sign-in link for Stayzy",
  "magic_link": "https://example.com/sign-in-preview",
  "expires_minutes": 15
}
```

The same preview data is saved in `test-data.json` for copying into SendGrid's
Test Data editor.

The preview URL is a placeholder. Use a real sign-in email to verify the final
flow. This file is an uploadable template; creating it does not update SendGrid.
