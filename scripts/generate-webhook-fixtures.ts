/**
 * Generate the webhook-signature fixtures the Python suite verifies against.
 *
 * WHY THIS SCRIPT EXISTS AT ALL.
 *
 * `posthaste/webhooks.py` is a reimplementation of the signing scheme, not a
 * binding to it — a Python package cannot import a TypeScript workspace module,
 * and vendoring one would be worse. So a Python test that signs with the Python
 * code and then verifies with the Python code proves only that the file agrees
 * with itself. It would pass just as happily if BOTH halves were wrong, and the
 * one case a customer actually cares about — verifying a genuine delivery from
 * our webhook worker — is precisely the case it would not cover.
 *
 * These fixtures are therefore produced by the REAL signer,
 * `packages/core/src/webhook-signature.ts`, which is the code that signs live
 * deliveries. Checked in, so the Python suite needs neither Node nor this
 * script to run.
 *
 * Regenerate after any change to the signing scheme:
 *
 *     pnpm exec tsx packages/sdk-python/scripts/generate-webhook-fixtures.ts
 *
 * The bodies below are chosen for the places a byte-level signature goes wrong:
 * non-ASCII text, an emoji outside the BMP, an embedded `</script>`, a body
 * whose whitespace and key order would not survive a JSON round trip, and an
 * empty body.
 */

import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { signWebhook } from '../../core/src/webhook-signature.js';

interface Fixture {
  readonly name: string;
  readonly note: string;
  readonly secret: string;
  readonly timestamp: number;
  readonly body: string;
  readonly header: string;
}

/*
 * Not real secrets, and they say so in the value itself.
 *
 * A realistic-looking `whsec_` blob would trip the repo's gitleaks scan for
 * ever and earn itself an allowlist entry — and an allowlist entry is exactly
 * what stops the scan noticing the day somebody pastes a live secret in here.
 * Spelling `example_secret` into the string keeps the scanner useful.
 */
const SECRET = 'whsec_example_secret_a1b2c3d4e5f6a7b8c9d0';
const OTHER_SECRET = 'whsec_example_secret_z9y8x7w6v5u4t3s2r1q0';

/** Fixed, so a regeneration produces a byte-identical file. */
const T = 1_760_000_000;

const bodies: Array<{ name: string; note: string; body: string; secret?: string }> = [
  {
    name: 'delivered',
    note: 'An ordinary delivered event, exactly as the worker serialises it.',
    body: JSON.stringify({
      id: 'evt_7Fk2mQpX9sLbN4tRvZa1Cd',
      type: 'delivered',
      createdAt: '2026-08-20T09:14:02.117Z',
      data: {
        messageId: 'msg_AZLm3kQ8T2Sf9pXbNc7HrQ',
        detail: { smtpCode: 250 },
        tags: ['receipt'],
        metadata: { orderId: '4471' },
      },
    }),
  },
  {
    name: 'bounced_unicode',
    note: 'Non-ASCII in the payload. The HMAC is over UTF-8 bytes, so a receiver that hashes a differently-encoded string fails here and nowhere else.',
    body: JSON.stringify({
      id: 'evt_unicode',
      type: 'bounced',
      createdAt: '2026-08-20T09:14:02.117Z',
      data: {
        messageId: 'msg_unicode',
        detail: { reason: 'Boîte aux lettres pleine — 郵便受けがいっぱいです 📮' },
      },
    }),
  },
  {
    name: 'script_tag',
    note: 'An embedded </script> and a lone backslash. Nothing here may be escaped, unescaped or normalised on the way into the HMAC.',
    body: '{"id":"evt_script","type":"complained","note":"</script><b>\\\\o/</b>"}',
  },
  {
    name: 'reserialisation_trap',
    note: 'Two spaces, a trailing newline and keys in an order json.dumps would not reproduce. Verifying a parsed-then-re-serialised body fails on exactly this, which is the most common support question about webhooks.',
    body: '{  "type":"delivered",\n  "id":"evt_spacing"  }\n',
  },
  {
    name: 'empty_body',
    note: 'A zero-length body still signs. The signed payload is the timestamp, a dot, and nothing.',
    body: '',
  },
  {
    name: 'wrong_secret',
    note: 'Signed with a DIFFERENT secret. Must be rejected as signature_mismatch — this is the forgery case.',
    body: JSON.stringify({ id: 'evt_forged', type: 'delivered' }),
    secret: OTHER_SECRET,
  },
];

const fixtures: Fixture[] = bodies.map(({ name, note, body, secret }) => ({
  name,
  note,
  // The secret the PYTHON side will verify with is always SECRET. The
  // `wrong_secret` case signs with another one on purpose, so verification has
  // to fail.
  secret: SECRET,
  timestamp: T,
  body,
  header: signWebhook(body, secret ?? SECRET, T),
}));

const here = dirname(fileURLToPath(import.meta.url));
const out = resolve(here, '../tests/fixtures/webhook_signatures.json');
mkdirSync(dirname(out), { recursive: true });

writeFileSync(
  out,
  `${JSON.stringify(
    {
      _generatedBy: 'packages/sdk-python/scripts/generate-webhook-fixtures.ts',
      _signedBy:
        'packages/core/src/webhook-signature.ts — the implementation that signs live deliveries',
      _regenerate: 'pnpm exec tsx packages/sdk-python/scripts/generate-webhook-fixtures.ts',
      fixtures,
    },
    null,
    2,
  )}\n`,
  'utf8',
);

console.log(`wrote ${fixtures.length} fixtures to ${out}`);
