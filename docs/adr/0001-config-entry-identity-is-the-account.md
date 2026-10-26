# Config entries are identified by the Account, not the Account Identifier

A CatLink Account can hold both an Email and a Phone, and the cloud allows only one Session per Account, so two config entries on the same Account would evict each other on every poll and duplicate every device. We therefore key a config entry on the Account's stable cloud id, read after sign-in, rather than on the Email or Phone that was typed. Adding the integration with a different Account Identifier for an Account that is already configured offers to switch that entry to the new identifier instead of creating a second entry.

Entries created before this decision are keyed `"{country code}-{phone}"`; they adopt the Account id on their next successful sign-in, so nobody has to reconfigure.

## Considered Options

- **Key on the Account Identifier** (the previous behaviour, extended to Email). Simplest and needs no migration, but it lets one Account be configured twice, which under the single-Session rule is a permanent self-inflicted Eviction loop.
- **Abort as "already configured"** when the Account matches. Correct, but it strands people whose Phone sign-in stopped working and who are trying to move to Email, which is the main reason Email sign-in is being added.
