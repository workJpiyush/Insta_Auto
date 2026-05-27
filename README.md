Overview
Relevant source files
The cjp_script repository is a specialized data collection toolset designed to scrape Instagram follower lists and enrich them with detailed profile metadata. It is built to support competitive analysis and bot detection by comparing a target account against multiple comparison accounts.

The system is architected around a two-stage pipeline:

Follower Extraction: Retrieving the list of unique identifiers (pk) and usernames following a specific handle.
Profile Enrichment: Fetching deep metadata (biography, follower counts, media counts, profile picture status) for the collected identifiers.
System Architecture
The codebase provides two distinct, interchangeable backends for interacting with Instagram data. Both backends produce and consume the same JSON schema within the data/ directory, allowing for hybrid workflows.

Logic Flow and Entity Mapping
The following diagram illustrates how the Python scripts map to the core scraping operations and data entities.

Diagram: Pipeline to Code Entity Mapping






<img width="1483" height="853" alt="image" src="https://github.com/user-attachments/assets/990fccff-d7d6-49fa-86a9-6923718f625e" />


















Sources: 
src/apify_scraper.py
1-25
 
src/scraper.py
1-30
 
enrich.py
1-25
 
discover.py
1-15

Two Scraping Backends
The repository supports two primary methods for data acquisition, each residing in its own source file.

Backend	Implementation File	Strategy	Primary Advantage
Apify	src/apify_scraper.py	Uses cloud-based actors via ApifyClient.	Multi-token rotation to bypass free-tier limits.
instagrapi	src/scraper.py	Direct Private API interaction via instagrapi.Client.	No external service dependency; lower latency for small sets.
Apify Backend (src/apify_scraper.py)
This backend utilizes the TokenPool class 
src/apify_scraper.py
41-42
 to manage multiple APIFY_TOKEN environment variables. It rotates tokens when hitting the PER_TOKEN_DAILY_ITEMS cap 
src/apify_scraper.py
27
 It delegates scraping to external actors:

FOLLOWER_ACTOR: scraping_solutions/instagram-scraper-followers-following-no-cookies 
src/apify_scraper.py
23
PROFILE_ACTOR: apify/instagram-profile-scraper 
src/apify_scraper.py
24
instagrapi Backend (src/scraper.py)
This backend performs direct authenticated requests. It resolves handles via user_info_by_username_v1 
src/scraper.py
50
 and fetches followers in paginated chunks of 200 
src/scraper.py
68-70

For a detailed comparison and help choosing between them, see Choosing a Backend: Apify vs. instagrapi.

Sources: 
src/apify_scraper.py
23-30
 
src/apify_scraper.py
41-60
 
src/scraper.py
36-70

Data Storage and Persistence
Both backends share a unified storage strategy in the data/ directory. Files are saved progressively to ensure that rate limits or crashes do not result in total data loss.

Diagram: Data Entity Relationships

Follower Lists: Saved as followers_{handle}.json 
src/scraper.py
24
Enriched Profiles: Saved as profiles_{handle}.json 
src/scraper.py
28
Token State: Apify token exhaustion is tracked in _token_state.json 
src/apify_scraper.py

