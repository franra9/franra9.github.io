### add new entry:

under `_posts`

### first time:

```
bundle install
```

### build page:

```
bundle exec jekyll build
```

### serve:

```
bundle exec jekyll serve
```

Preview at http://localhost:4000. GitHub Actions also runs `jekyll build` on every push.

### English translation PR:

Add a repository secret `DEEPL_API_KEY` (preferred) or `OPENAI_API_KEY`.

The **Translate English (PR)** workflow runs on content changes and from Actions → workflow_dispatch. It writes English copies and opens a pull request on `translate/english` for review. Nothing is published until that PR is merged.

English URLs after merge: `/en/`, `/en/cv/`, `/en/resume/`.
