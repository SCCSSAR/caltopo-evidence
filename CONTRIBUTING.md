# Contributing

Bug reports and fixes are welcome, particularly from other SAR teams running this against maps that look different from ours. The constraints below are not style preferences. Each one is load-bearing for a tool whose output may end up in a case file.

## Run the tests

```bash
python3 -m unittest discover -s tests
```

No network, no credentials, no third-party packages. 77 tests, and they run in well under a second. CI runs the same command on Python 3.9 through 3.13.

## A passing test proves nothing

The standard here is mutation testing: break the code on purpose and confirm the test fails. A test that passes against correct code and also passes against broken code is worse than no test, because it reports coverage it does not have.

This is not theoretical. The manifest's file-size check leaked its first mutation, and it turned out to be redundant with the digest check for detection purposes. Rather than inflate the test, it now pins what the check actually delivers: a truncated file gets reported as truncation rather than as a generic digest mismatch.

If you add a check, break it and watch a test go red before you open the PR. Say in the PR which mutations you ran.

## Constraints that need a conversation before you change them

**Standard library only.** An evidence tool's dependency tree is part of its audit surface. Every package added is code someone has to account for when asked how a bundle was produced, and it narrows the Python versions the tool can support. If you genuinely need a dependency, open an issue first and make the case.

**Read-only.** No POST, PUT, or DELETE. See SECURITY.md.

**`exif.py` stays small.** It answers one question: did the camera record a position? It is not a general EXIF library and it should not become one. Those bytes come off the internet.

**Photo and marker coordinates stay in separate columns.** A responder can drop a marker and then walk before taking the photo. On the map used for validation the gap was about 1.6 m. They are different facts, and merging them would assert a precision the data does not carry.

**A missing coordinate is never inferred.** A photo with no camera fix keeps an empty `latitude` cell even when its marker has a position. The marker's coordinate goes in the marker's column, where a reader can see what it is. "The photo was taken here" and "someone later said it was taken here" are different claims, and only one of them is a measurement.

**Downloading is the default.** `extract` downloads the photos, because that is what the command says it does. `--metadata-only` is the opt-out.

## Never commit real map data

No map IDs, no incident photographs, no real coordinates, and no evidence item names in test fixtures, docstrings, examples, or documentation.

Watch the fixtures in particular. A six-decimal coordinate is roughly 10 cm of precision, which no hand-invented placeholder ever needs, so full-precision numbers in a test file are a reliable sign that real data got pasted in. The same goes for photo titles: a filename that reads like something a searcher actually found probably is.

Keep the reasoning, replace the nouns. If a test exists because of something real that happened on a real map, say so and describe the shape of it. The fact that two titles differed only by case is the useful part. Which two, is not.
