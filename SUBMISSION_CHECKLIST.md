# Final submission checklist

The implementation and executable evidence are complete. The following actions
require the team's GitHub account or a manual desktop UI and therefore are not
automated in this workspace.

## 1. Capture the two UI images

1. Open Neo4j Browser at <http://localhost:7474> and authenticate with the
   password stored in ignored `.env`.
2. Run:

   ```cypher
   MATCH p=(source:CPGNode)-[edge:CPG_EDGE]->(target:CPGNode)
   RETURN p
   LIMIT 50;
   ```

3. Save the graph screenshot as `book/figures/neo4j-browser.png`.
4. Open MongoDB Compass with `mongodb://localhost:27017`, select
   `lab04.source_metadata`, and filter on:

   ```json
   {"repo_id": "huggingface/optimum"}
   ```

5. Save the document screenshot as `book/figures/mongodb-ui.png`.
6. Regenerate and rebuild:

   ```powershell
   python scripts/generate_book.py
   jupyter-book build --html --strict
   ```

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
