'use strict';
// Single-candidate specialization of micromatch 4.0.8's ordered list selection.
// Copyright (c) 2014-2024 Jon Schlinkert; MIT (LICENSE-micromatch).
// No braces expansion/compiler is used. picomatch owns maintained glob parsing.
const picomatch = require('picomatch');

module.exports = function workspaceMatch(relativePath, patterns) {
  const ordered = [].concat(patterns);
  let negatives = 0;
  let seen = false;
  let kept = false;
  let omitted = false;
  for (const value of ordered) {
    const pattern = String(value);
    // A malformed/deep workspace declaration must fail install, not overflow the
    // stack, consume unbounded regex compiler work, or silently select a parent.
    if (pattern.length > 65536) throw new RangeError('Workspace pattern is too long');
    let depth = 0;
    for (let i = 0; i < pattern.length; i++) {
      if (pattern[i] === '\\') { i++; continue; }
      if (pattern[i] === '{' || pattern[i] === '(') {
        if (++depth > 128) throw new RangeError('Workspace pattern nesting exceeds 128');
      } else if (pattern[i] === '}' || pattern[i] === ')') {
        depth = Math.max(0, depth - 1);
      }
    }
    const matcher = picomatch(pattern, { onResult() { seen = true; } }, true);
    const negated = matcher.state.negated || matcher.state.negatedExtglob;
    if (negated) negatives++;
    const result = matcher(relativePath, true);
    const matches = negated ? !result.isMatch : result.isMatch;
    if (!matches) continue;
    if (negated) omitted = true;
    else { omitted = false; kept = true; }
  }
  return (negatives === ordered.length ? seen : kept) && !omitted;
};
