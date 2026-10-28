# Request sequence diagram

This describes the current JSON inbox workflow. Solid arrows hand work to another
part, dashed arrows return results, and each caller waits for the result before
continuing. Emails, downloads, and document reviews run sequentially.

```mermaid
sequenceDiagram
    participant CLI as Command (plan_cli.py)
    participant Inbox as Inbox runner (email_flow.py)
    participant App as Request handler (planner.py / review.py)
    participant AI as Gemini
    participant Browser as Browser helpers (probe.py / inventory.py / downloads.py)
    participant Site as UARB website
    participant PDF as PDF reader (content.py)

    CLI->>CLI: Read --emails-json and validate options and the entire inbox
    Note over CLI: Invalid input exits before browser calls, AI calls, or output creation
    CLI->>Inbox: Await process_emails with inbox text and browser settings
    Inbox->>Inbox: Revalidate inbox and create a unique batch directory

    loop Each email ID in input order
        Inbox->>App: Await plan_email with subject and body
        App->>App: Create workflow directory and initial plan.json
        App->>AI: Interpret subject and body
        AI-->>App: Matter, categories, count, criteria, download and review requests
        App->>App: Validate interpretation against the email and supported categories

        alt Request needs clarification
            App-->>Inbox: Saved checkpoint and clarification question
        else Request can proceed
            Note over App: Infer actions from email, review implies downloads, limit is at most 10
            App->>App: Save checked request and execution mode in plan.json
            App->>Browser: Await complete inventory for this matter and categories
            Browser->>Site: Search and verify matter, read metadata and all category counts
            Site-->>Browser: Matter identifier, metadata, and counts
            loop Each requested nonempty category
                Browser->>Site: Open category and scroll through every row
                Site-->>Browser: Document numbers, titles, dates, and list order
            end
            Note over Browser: Check completeness and merge identical records across categories
            Browser-->>App: Complete inventory and browser report after session closes
            App->>App: Check inventory status and checkpoint plan.json

            opt Inventory is not empty
                App->>AI: Rank real inventory IDs using request, metadata, and result limit
                AI-->>App: Ordered candidate IDs and selection reasons
                App->>App: Reject invented or duplicate IDs and excess selections
            end
            App->>App: Save completed metadata plan in plan.json

            alt Downloads requested and shortlist is not empty
                loop Fresh retrieval sessions until done or retry budget exhausted
                    App->>Browser: Download remaining exact selected records
                    Browser->>Site: Reverify matter and lists, match number, title, and date
                    Site-->>Browser: Current records and requested file downloads
                    Note over App,Browser: Each validated download immediately checkpoints review.json
                    Browser-->>App: Saved files, checksums, current counts, and failures
                    Note over App: Retry temporary timeouts with backoff in a fresh session
                end

                opt Content review requested
                    loop Each successful download in original ranking order
                        App->>App: Recheck file checksum
                        App->>PDF: Await bounded PDF-reading subprocess
                        PDF-->>App: Text by physical page, coverage, and extraction status
                        App->>App: Checkpoint extraction metadata in review.json
                        opt Extraction is complete and text is within limits
                            App->>AI: Review this document's labelled text excerpts and request
                            AI-->>App: Findings, supporting excerpt IDs, and limitations
                            App->>App: Resolve quotes and pages, validate citations, checkpoint result
                        end
                    end
                end
                App->>App: Recheck successful files and create their ZIP if any exist
                App->>App: Save final status and document results in review.json
                App-->>Inbox: Document results, coverage, errors, and local file paths
            else Metadata only or no selections
                App-->>Inbox: Recommendations or empty results and checkpoint paths
            end
        end

        Inbox->>Inbox: Format readable reply from checked results without another AI call
        Inbox->>Inbox: Preserve email ID and atomically replace batch replies.json
        Note over Inbox: Save this reply before processing the next email
    end
    Inbox-->>CLI: Batch directory, replies, and aggregate exit code
    CLI->>CLI: Print replies.json path and exit
```

The command is `senpilot-plan --emails-json PATH`. Count, downloads, and review
come from each email's wording. Metadata-only requests stop after ranking.
Download-only requests retrieve files and create a ZIP without extraction or
content-review calls. Review requests also extract and review eligible PDFs.

The diagram shows the normal path and request clarification. Inventory errors or
incomplete lists stop that email before ranking. Other ordinary processing errors
become failure replies, and the next email still runs. Individual download or
review failures preserve completed work and are reported in the reply. A failure
to write `replies.json` stops the batch. Exit codes are `0` for completion, `2`
for clarification, and `1` for failures or partial results, with failures taking
precedence across the batch.

Gemini calls allow at most three attempts for transient service failures, with
5/15-second backoff and bounded `Retry-After` handling. Selected-file timeouts
close the browser session and get at most three attempts per file. Unattempted
files go before stalled files in recovery sessions. Changed, missing, or ambiguous
records are not substituted. Unsupported or incomplete PDF extraction skips the
content-model call, and rejected citations are not published as findings.

`replies.json` is the readable output and includes findings, quotes, physical PDF
page citations, coverage warnings, failures, and local file/ZIP paths when
available. `plan.json`, `review.json`, and `extracted/*.json` are internal
checkpoints. Browser diagnostics are JSON reports, page text, and Playwright
traces. There are no standalone HTML/PNG captures or generated Markdown review
reports. The runner reads and writes local JSON files and does not send email.

Checkpoint resume is available through the internal `resume_email()` Python API,
not through a CLI option. It verifies saved evidence and reuses completed work.
