# AGENTS.md

이 문서는 이 레포에서 작업하는 AI 에이전트(Claude Code 등)가 따라야 할 규칙이다.

## 1. 응답 언어
- 사용자에게 하는 모든 응답, 설명, 질문, 작업 요약은 한국어로 작성한다.
- 사용자가 영어로 질문하거나 영어 자료·로그를 다룰 때도 응답은 한국어로 한다.
- 코드, 명령어, 파일 경로, 에러 메시지, 라이브러리·API 이름은 번역하지 않고 원문 그대로 쓴다.
- 코드 주석은 기존 파일의 언어를 따른다.
- 커밋 메시지는 기존 히스토리 형식(`<type>: <설명>`)을 따른다.

## 2. 작업 시작 시
작업을 시작하면 먼저 아래를 실행한다.

```bash
git config --local user.name "wer134"
git config --local user.email "87606833+wer134@users.noreply.github.com"
git config core.hooksPath .githooks
```

## 3. Git 커밋 규칙

### 작성자
- 모든 커밋의 author/committer는 `wer134 <87606833+wer134@users.noreply.github.com>`이다.
- `Claude <noreply@anthropic.com>` 등 AI·봇 계정으로 커밋하지 않는다.

### 커밋 메시지
- 형식: `<type>: <설명>`
  - type: `feat`, `fix`, `docs`, `results`, `chore`
- 다음 내용을 넣지 않는다.
  - `Co-Authored-By: Claude ...`
  - `Generated with Claude Code`
  - `Claude-Session: https://claude.ai/code/session_...`
  - 그 외 AI 도구, 에이전트, 세션 링크를 언급하는 문구
- PR 제목과 설명에도 위 문구를 넣지 않는다.

### 브랜치
- 브랜치 이름에 `claude/` 등 도구 이름 접두사를 쓰지 않는다.
- 형식: `feat/xxx`, `fix/xxx`, `docs/xxx`, `results/xxx`

### 커밋 후 확인
커밋할 때마다 아래로 확인한다.

```bash
git log -1 --format='%an <%ae> | %cn <%ce>%n%B'
```

작성자가 위 계정이 아니거나 메시지에 금지 문구가 있으면 push 전에 `git commit --amend`로 고친다.

### 금지 사항
- 사용자 지시 없이 `git push --force`, `git filter-repo`, `git rebase`로 히스토리를 다시 쓰지 않는다.
- 사용자 지시 없이 브랜치를 삭제하지 않는다.
