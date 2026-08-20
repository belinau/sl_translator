---
name: no-unverified-success-claims
description: "Don't claim verified/fixed/works based on a test that doesn't reproduce the user's reported failure environment"
condition: ["Verified working end-to-end", "Proof it works", "works end-to-end", "confirmed working", "headless verification", "I've proven", "Decisive fix", "Found it\\."]
scope: "text"
---

Stop. You are about to claim the work is verified/fixed/working. In this session that claim was made ~10 times and was false every time because the test (headless browser, localhost, a sample file) did NOT reproduce the user's actual failing environment.

Before asserting success you MUST hold two facts: (1) the test exercised the EXACT failure path the user reported (same access URL/device, same real browser file-picker, same file), and (2) the user's own terminal/browser confirms it. A headless `input.uploadFile()` that passes proves nothing when the user's real-browser multipart upload is what's failing.

If you only have a non-reproducing test, say exactly that — 'passes in my headless test, but I have not reproduced your browser's failure, so this is unconfirmed' — and keep debugging, never declare done.

Also: when a traceback shows the failure is INSIDE a library/framework call (e.g. `nicegui/.../upload.py` `request.form()`) and your own code is NOT in the stack, STOP rewriting your code. The bug is upstream of your handler; editing your handler again is the definition of a wrong assumption. Either instrument the library path, isolate the variable you cannot see, or state plainly what evidence you still need.