# Support

AtomLearn is a community-maintained open-source project. Support is best-effort
and does not include guaranteed response times, tutoring services, legal advice,
medical advice, or validation of a claimed learning effect.

## Where to ask

- Use [GitHub Issues](https://github.com/panjose/Atom-Learn/issues) for
  reproducible bugs and concrete feature requests.
- Use [GitHub Discussions](https://github.com/panjose/Atom-Learn/discussions)
  for usage questions, workflow ideas, course-design patterns, and research or
  exam use cases once Discussions is enabled.
- Use GitHub private vulnerability reporting for security or privacy issues; see
  [SECURITY.md](SECURITY.md).

Include the AtomLearn version, operating system, Python version, install path
(signed Manager or unmanaged source), runtime profile, exact command, minimized
input shape, and redacted output. Do not attach `.atomlearn/` state, private
materials, learner answers, credentials, or release keys.

## Before requesting help

```powershell
atomlearn version
atomlearn migrate status
atomlearn validate <workspace>
atomlearn-manager bootstrap status
atomlearn-manager doctor
```

Run only the commands relevant to your installation. A learning session may
explain an update or profile blocker, but it must never apply a release, profile,
migration, recovery, or rollback operation on the user's behalf.
