# Future ideas

Ideas for draftagram that came out of using it, with the reasoning, so they can be picked up
later. When one is built, move what's lasting into README.md and delete it here.

## Ask Claude from the page

**The need.** While reviewing a draft, the questions that come up are about the code itself:
what a method is for, what the original code did, and why the draft changes it. Today the
reviewer has to switch to the terminal and describe what they're looking at in words, which
is exactly the gap the page was built to close.

**The idea.** An "Ask Claude" box on a class, member, file or diff line. The question goes to
the Claude session that made the draft along with what it's about (the class and member, or
the file and line in the draft's version). The answer appears on the page beside that thing.

**How it would fit.** It can use the same route as the Fix/Leave answers: the page saves the
question with the server, and `draft answers --wait`, which Claude already runs in the
background, returns with it. Claude answers in the notes (a new field, say `answers`, keyed
by what was asked about), and the page redraws with the reply. There is still only one Claude,
the session that ran `/draft`, so it answers with everything it read and decided while
drafting.

**To work out.** Whether answers that turn out to matter (a method's purpose, a decision)
should be offered for the commit body or the ticket, so the next reader gets them too (see
step 5 of the skill). How a question that changes Claude's mind becomes a new round of the
draft rather than only an answer.
