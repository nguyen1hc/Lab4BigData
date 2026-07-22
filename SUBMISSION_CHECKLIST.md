# Final submission checklist

Complete the automated evidence sequence below before publishing. The final
GitHub Pages enablement and incognito review still require the team's account.

## 1. Capture one consistent evidence run and the two UI images

1. With all Docker services running and the 61-file repository state loaded, run:

   ```powershell
   python scripts/capture_replay_evidence.py
   ```

   Continue only when all 16 replay assertions report `true`.
2. Open Neo4j Browser at <http://localhost:7474> and authenticate with the
   password stored in ignored `.env`.
3. Run a graph query scoped to the replay file ID printed in
   `evidence/runtime/verification.json`, then save the result as
   `book/figures/neo4j-browser.png`.
4. Open MongoDB Compass with `mongodb://localhost:27017`, select
   `lab04.source_metadata`, and filter on the replay file `_id`. Save the final
   document as `book/figures/mongodb-ui.png`.
5. Execute the notebooks, validate provenance, and build:

   ```powershell
   python scripts/generate_book.py
   python scripts/validate_notebooks.py
   jupyter-book build --html --strict
   ```

6. Confirm that notebook metadata references the SHA-256 of the same
   `verification.json` and that no notebook contains a traceback.

## 2. Publish the book

1. Create an empty **public** GitHub repository owned by the team.
2. Add its URL and push the existing `main` commit:

   ```powershell
   git remote add origin https://github.com/TEAM/LAB04-REPOSITORY.git
   git push -u origin main
   ```

3. In repository settings, select **GitHub Actions** as the Pages source.
4. Wait for the `Publish Jupyter Book` workflow to finish successfully.
5. Open the Pages root URL in an incognito window and test all eight pages,
   images, navigation links, and code-output blocks.
6. Submit exactly the public Pages root URL to Moodle.

Do not commit `.env`, `source-repo/`, SQLite state, Docker volumes, or `_build/`.
