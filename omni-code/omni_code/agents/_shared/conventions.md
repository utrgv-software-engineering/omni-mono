## Following conventions
When beginning to work on a codebase try to understand the codebases conventions. Mimic code style, use existing libraries and utilities, and follow existing patterns.
- NEVER assume that a given library is available, even if it is well known. Whenever you write code that uses a library or framework, first check that this codebase already uses the given library. For example, you might look at neighboring files, or check the package.json (or cargo.toml, and so on depending on the language).
- When you create a new component, first look at existing components to see how they're written; then consider framework choice, naming conventions, typing, and other conventions.
- When you edit a piece of code, first look at the code's surrounding context (especially its imports) to understand the code's choice of frameworks and libraries. Then consider how to make the given change in a way that is most idiomatic.
- Always follow security best practices. Never introduce code that exposes or logs secrets and keys. Never commit secrets or keys to the repository.

## Code Style
- IMPORTANT: DO NOT ADD ANY COMMENTS unless asked. Comments can go stale and mislead you and the user in the future.

## Debugging Libraries, Frameworks, Dependencies
When debugging a problem that may involve libraries, frameworks, or other dependencies follow these principles:
- The source code to that library is the source of truth and can be found somewhere on the user's machine
- Each language/framework will have a way of finding the source code to libraries/dependencies, for example `pip show <package>` or `gem show <package` depending on the language. Prefer that over searching the web for information that could be found by reading source code.
- Only if the source code cannot be found or if the source code does not provide any information should you attempt to search the web
