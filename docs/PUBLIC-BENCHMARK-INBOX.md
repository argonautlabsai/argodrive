# Public benchmark inbox

Argodrive uses `benchmarks@argonautlabs.ai` as the public destination for user-submitted SSD benchmark reports.
The app opens a message in the user's mail client with a short redacted summary; it never sends mail in the
background. Users should review attachments before sending.

The address is only active after it has been created with the domain's mail provider. The quickest setup is an
alias or forwarding rule to a monitored mailbox. A full hosted mailbox is optional. Configure the provider's MX
and SPF/DKIM records before publishing the address on the website or in a beta release, then send a test message
from an external account and verify delivery.

Keep the intake address separate from a personal mailbox. Do not ask users to send raw logs by default: the app's
email summary omits local paths, volume UUIDs and drive serial numbers. Raw JSON and text exports can contain
diagnostic details and must be reviewed before attaching.
