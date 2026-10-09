# Historical Job-Requirement Change Tracking: Existing Work, Gaps, and a Practical MVP

## Executive finding and the five closest approaches

The idea is **worth continuing, but it should be narrowed before it becomes a platform**. The difficult and differentiated part is not collecting current jobs or extracting skills. Those pieces already exist. The credible gap is a system that can say, with evidence:

> “For this employer and this narrowly defined role family, requirement X became more common, requirement Y disappeared, and responsibility Z shifted during the last six to twelve months — after controlling for reposts, title changes, seniority, location, and changes in the mix of openings.”

I found mature components for almost every individual stage: historical job-ad data, live posting feeds, deduplication, occupation normalization, skill extraction, taxonomy linking, and labor-market trend analysis. I did **not** find, among the sources reviewed, an open system that combines all of these into **company × role-family longitudinal comparisons with revision-aware evidence, uncertainty, and job-seeker-oriented “what should I learn next?” guidance**. That conclusion is necessarily a finding from the reviewed landscape, not proof that no private product has such functionality.

The closest approaches are:

| Rank | Existing project / approach | Why it is close | What it does not solve |
|---|---|---|---|
| **1** | **Lightcast Job Postings + Skills** — [US Job Postings docs](https://docs.lightcast.io/data-shares/docs/us-job-postings) | Commercially mature historical job-posting intelligence. U.S. posting data go back to January 2010 and can be filtered by company, job title, skills, location, experience, time period, and full text. Lightcast distinguishes unique from duplicate postings, making it the closest data/product analogue to your concept. citeturn25view2 | The documented product is primarily a labor-market data product, not an open source “same company/same role changed from A to B” career-intelligence engine. Raw historical microdata are not an open public corpus; recent research using Lightcast describes the underlying microdata as licensed institutional data. citeturn25view10 |
| **2** | **JobTech Sweden Historical Ads + JobStream** — [official dataset page](https://data.jobtechdev.se/dataset/job-ads) | The strongest open longitudinal substrate I found. JobTech provides historical Swedish postings, a historical API, and a real-time stream containing publication, unpublication, and advertisement-text-update events. The historical file contains roughly 6.9 million postings going back to 2006, and the relevant job-ad resources are CC0. citeturn25view0 | Sweden/Platsbanken coverage rather than a broad U.S. tech-company universe; it supplies data and events, not longitudinal company-role analysis. |
| **3** | **Cedefop Skills-OVATE / Eurostat Web Intelligence Hub OJA pipeline** — [Skills-OVATE](https://www.cedefop.europa.eu/en/tools/skills-online-vacancies) / [WIH methodology](https://cros.ec.europa.eu/wih/oja) | A production-scale longitudinal online-job-advertisement architecture: multiple collection routes, cleaning, deduplication, ontology/ML extraction, quality validation, and trend statistics. It is perhaps the best methodological analogue for building reliable trend measures. citeturn25view1 | Public output is mainly aggregated; access to the detailed microdata is restricted for approved statistical/scientific use and is not downloadable as an unrestricted job-ad corpus. citeturn25view1 |
| **4** | **Longitudinal labor-economics research using vacancy text** — especially Atalay et al. and Acemoglu et al. | This literature demonstrates that requirement/task content can genuinely change *within occupations, job titles, and establishments*, rather than changes merely reflecting different jobs being advertised. Atalay et al. find substantial historical task change within narrow job titles; Acemoglu et al. study establishment-level postings and find AI-adopting establishments changing the skill requirements of remaining vacancies. citeturn25view7turn25view9 | Research outputs are not reusable career products, and much modern work relies on commercial vacancy microdata. |
| **5** | **Nesta Open Jobs Observatory / OJD DAPS Skills Extractor** — [GitHub](https://github.com/nestauk/ojd_daps_skills) / [model card](https://nestauk.github.io/ojd_daps_skills/model_card/) | A working open-source implementation of one of your hardest NLP steps: it finds skill and experience spans in job ads and maps skills to standard taxonomies using NLP plus embedding similarity. It is installable as a Python package and supports ESCO and Lightcast Open Skills mappings. citeturn25view5 fileciteturn4file0 | It processes individual ads; it does not collect history, identify equivalent roles over time, distinguish revisions from new openings, or calculate longitudinal changes. |

**The strategic implication:** Lightcast demonstrates that customers value historical posting/skill intelligence; JobTech demonstrates that genuinely open longitudinal posting data can exist; Skills-OVATE demonstrates how much cleaning and validation are required; the economics literature demonstrates that within-role and within-establishment change is a meaningful object of study; and Nesta demonstrates that much of the extraction layer need not be built from scratch. citeturn25view0turn25view1turn25view2turn25view5turn25view7turn25view9

Your best differentiation is therefore **not “job-posting analytics” and not “AI skill extraction.”** It is the combination of **role equivalence + temporal identity + requirement-delta detection + evidence + job-seeker interpretation**.

## Projects, datasets, and reusable implementations

### Comparison of the most useful existing work

| Project / resource | Status and access | What actually works | Methods / architecture | Data | Main limitation for your idea | Reusable component |
|---|---|---|---|---|---|---|
| **Lightcast US Job Postings** — [docs](https://docs.lightcast.io/data-shares/docs/us-job-postings) | Commercial/authenticated data product; not an open historical corpus | Query historical demand by company, title, skill, occupation, geography, experience and timeframe; exposes unique versus duplicate posting metrics. U.S. history starts Jan. 2010 and is updated daily. citeturn25view2 | Vendor collection, parsing, classification and deduplication; official docs expose both unique and duplicate counts. citeturn25view2 | Very large historical U.S. vacancy corpus; recent academic work reports 433.6M Lightcast postings for 2010–2024 and says the underlying microdata require licensed institutional access. citeturn25view10 | Access/cost and reproducibility. Public docs do not constitute an open license to the posting microdata. | Benchmark for schema, filters, dedup semantics and commercial demand. |
| **JobTech Historical Ads / JobStream** — [official page](https://data.jobtechdev.se/dataset/job-ads) | **CC0**, public APIs/files | Historical file covers postings since 2006; Historical Ads API covers historical unpublished ads from 2016 onward; JobStream emits current ad events including text updates. citeturn25view0 | Structured APIs + event stream; postings enriched with occupation/competency metadata. citeturn25view0 | Arbetsförmedlingen/Platsbanken, Sweden | Geographic/language and source coverage, not methodology. | Best open dataset for proving the entire temporal pipeline without first negotiating a data contract. |
| **Cedefop / Eurostat WIH OJA** — [methodology](https://cros.ec.europa.eu/wih/oja) | Public aggregate outputs; detailed microdata controlled | Large-scale collection and processing of millions of job advertisements. The pipeline uses scraping, crawling and API agreements; duplicates are detected from descriptions, location and metadata and merged. Information extraction combines ontologies and ML; validation includes rules, statistical review and gold-standard feedback. citeturn25view1 | Multi-stage ingestion → cleaning/dedup → classification/extraction → validation | European OJAs, collected on an ongoing quarterly basis | Microdata access is restricted; public trend views are not a company-level historical diff corpus. WIH itself warns about representativeness, classification errors and ontology gaps. citeturn25view1 | Excellent reference architecture and quality-control design. |
| **Nesta `ojd_daps_skills`** — [repo](https://github.com/nestauk/ojd_daps_skills) | Working Python package, **MIT**; latest recorded code push in the inspected repository metadata was June 2025. fileciteturn3file0 | Extracts `SKILL`, `EXPERIENCE` and related spans; maps skills to ESCO, Lightcast Open Skills or a test taxonomy; installable with `pip install ojd-daps-skills`. fileciteturn4file0 | spaCy NER, an SVM for multi-skill spans, rules, sentence-transformer embeddings and cosine-based taxonomy matching. The package depends on spaCy, scikit-learn, sentence-transformers, Torch and Transformers. citeturn25view5 fileciteturn7file0 | Model training set reported in its model card: 500 job advertisements and 8,971 entities. citeturn25view5 | No temporal layer. Model-card skill span F1 is about 0.61 under its partial-match evaluation, so raw extraction should not be treated as perfect ground truth. citeturn25view5 | Strong baseline extractor and taxonomy mapper. |
| **SkillSpan** — [repo](https://github.com/kris927b/SkillSpan) | Working research code/data, **MIT**; NAACL 2022 published paper. Repository had a code push in Feb. 2025 when inspected. fileciteturn10file0 | Reproducible train/predict/evaluate scripts; releases two of its three de-identified data subsets in CoNLL and JSON formats and links pretrained models. fileciteturn11file0 | BERT-family span extraction; domain-adapted job-language models; experiments with long-span models and multitask learning. fileciteturn11file0 | 14.5K sentences and over 12.5K expert-annotated spans from three job-posting sources; only two sources publicly released. fileciteturn11file0 | Research stack is older than a modern production stack; its README still documents older Python/Torch experiment requirements, although the MaChAmp dependency was updated later. fileciteturn11file0 | Gold extraction data, annotation guidelines, model baselines. |
| **Tabiya Livelihoods Classifier** — [repo](https://github.com/tabiya-tech/tabiya-livelihoods-classifier) | Active open-source system, **MIT code**; inspected repository was pushed Oct. 4, 2026. fileciteturn14file0 | Full entity-linking pipeline and API/application. Extracts **Occupation, Skill, Qualification, Experience and Domain**, then resolves occupations/skills against ESCO via vector search. fileciteturn15file0 | Transformer NER → entity linking/vector similarity; multilingual configuration; API/service architecture | ESCO-derived resources, SkillSpan-related evaluation sets and other evaluation data; README states its datasets are CC BY 4.0 where applicable. fileciteturn15file0 | Current Hugging Face extraction-model access is described as token-gated through the administrators, reducing turnkey reproducibility despite open code. Spanish job NER is also explicitly identified as a known gap. fileciteturn15file0 | Most directly reusable full-stack entity/extraction architecture, especially for experience and qualifications. |
| **WorkRB** — [repo](https://github.com/techwolf-ai/workrb) | Active open-source benchmark, **Apache-2.0**; repository created in 2025 and pushed in July 2026 in the inspected metadata. fileciteturn12file0 | `pip install workrb`; standardized tests for job→skill, skill extraction, job normalization, job-title similarity, skill normalization and related work-domain tasks. fileciteturn13file0 | Sentence-transformer bi-encoders, JobBERT/ConTeXT models, plus TF-IDF, BM25, edit-distance and random baselines; reports ranking metrics including MAP, MRR, nDCG, recall and confidence intervals. fileciteturn13file0 | Multiple job/skill benchmark datasets and languages | It evaluates components rather than performing temporal trend inference. | Very useful evaluation harness for role normalization and skill linking instead of inventing benchmarks yourself. |
| **SkillNER** — [repo](https://github.com/AnasAito/SkillNER) | Working **MIT** Python package, but latest code push in inspected metadata was Jan. 2024. fileciteturn16file0 | Pip-installable skill/certification extraction with a live/demo-oriented interface. fileciteturn17file0 | spaCy + PhraseMatcher + exact/ngram matching against an EMSI-derived skill DB | No historical job corpus included | Maintainers explicitly caution that it has not reached full stability; dictionary matching is also less suited to novel/implied requirements. fileciteturn17file0 | Good cheap lexical/rule baseline. |
| **Howell et al. skill-dynamics dataset** — [preprint](https://arxiv.org/abs/2606.09918), [repository](https://github.com/AntJam-Howell/skills-econ-geog-data) | **2026 preprint**, not a peer-reviewed journal publication in the source reviewed; released aggregate data are CC BY 4.0. citeturn25view10 | A reproducible public longitudinal skill-demand panel with year-over-year dynamics and an interactive dashboard. citeturn25view10 | Streams raw postings, extracts skills/employer type, aggregates, then builds specialization, relatedness, diversity, complexity and churn variables. citeturn25view10 | Derived from 433.6M Lightcast postings, 2010–2024; output is 47,891 county-year observations with 201 variables. citeturn25view10 | Aggregation destroys the individual company-role-posting histories that your product needs. The authors explicitly flag a 2017–2018 vendor-methodology break and low-volume noise. citeturn25view10 | Excellent model for longitudinal validation, data-version caveats and trend statistics. |

### Historical data: what is realistically accessible

There is a sharp distinction between **historical labor-market statistics** and **historical individual advertisements**.

**JobTech is the best clean open-data answer.** Its official service exposes a historical file containing all Platsbanken advertisements since 2006, a historical API for unpublished advertisements from 2016 onward, and JobStream for live publication/unpublication/update events. The relevant resources are marked CC0. This makes it possible to build and release a reproducible proof of concept without scraping first. citeturn25view0

**Lightcast is the strongest U.S. historical source I found, but access is the problem.** Its official U.S. data documentation says coverage extends to January 2010, is updated daily, and supports company, title, skill, location, experience and timeframe filters. It explicitly exposes unique and duplicate posting counts. Separately, Howell et al. report transforming 433.6 million Lightcast postings from 2010–2024, but state that the raw microdata are licensed and require institutional access. citeturn25view2turn25view10

**Cedefop/Eurostat has enormous European coverage but intentionally controlled microdata access.** The Web Intelligence Hub describes millions of online job ads processed quarterly, but detailed microdata access is restricted to approved statistical/scientific contexts rather than being an unrestricted downloadable historical corpus. citeturn25view1

**Common Crawl can help reconstruct history, but it is raw archival infrastructure rather than a job-posting dataset.** It has collected petabytes of web data regularly since 2008, provides raw pages, metadata and extracted text, and maintains URL indexes so specific pages can be located. It is free to access, including through cloud infrastructure. citeturn25view3 This is useful when a company career page or ATS URL was crawled, but you inherit all of the hard work: URL discovery, page parsing, temporal identity, duplicate handling and determining whether the capture actually represents the posting while it was active.

**The Wayback Machine is useful as opportunistic backfill, not as your primary database.** Internet Archive explicitly notes that sites may be absent because the crawler did not know about them, the site was inaccessible, robots rules applied, or material was excluded. “Save Page Now” captures a specific page once; it does not schedule future captures or recursively archive a whole site. Archived pages can also have missing resources. citeturn25view4 Those properties make Wayback evidence valuable when it exists but dangerous as a denominator for statements such as “this company did not require Kubernetes in April.”

For ongoing collection, public ATS interfaces are much more attractive than reverse-engineering arbitrary HTML. Lever publishes a [Postings API](https://github.com/lever/postings-api); Greenhouse documents its [Job Board API](https://docs.greenhouse.io/job-board.html); and Ashby exposes a [Public Job Posting API](https://developers.ashbyhq.com/docs/public-job-posting-api). These are principally mechanisms for retrieving **currently published** jobs, so their real value to your project is to begin saving your own immutable snapshots now. They should not be presented as historical archives.

That leads to a practical two-track data strategy:

**Historical proof:** JobTech Sweden or a licensed Lightcast sample.

**Future moat:** daily or weekly snapshots from public company/ATS feeds, preserving every observed version of a posting.

## Research papers and implementation-oriented lessons

The academic literature is unusually supportive of the underlying premise: job text does change meaningfully through time, including *within narrowly defined jobs and establishments*. The methodological challenge is attributing that change correctly.

### Foundational longitudinal work

**Atalay, Phongthiengtham, Sotelo & Tannenbaum, “The Evolution of Work in the United States,” AEJ: Applied Economics, 2020 — published.**  
[Publisher page](https://www.aeaweb.org/articles?id=10.1257/app.20190070)

The paper constructs a historical job-advertisement text dataset covering 1950–2000. Its key result for your design is that a substantial fraction of shifts away from routine cognitive/manual work and toward nonroutine interactive/analytic work happens **within narrowly defined job titles**, rather than simply because the occupation mix changes. The AEA also provides a replication package. citeturn25view7

**Relevance:** this is direct precedent for treating description text within a normalized role as a longitudinal object. It also warns against measuring change solely from occupational counts.

**Deming & Noray, “Earnings Dynamics, Changing Job Skills, and STEM Careers,” Quarterly Journal of Economics, 2020 — published.**  
[Publisher](https://academic.oup.com/qje/article/135/4/1965/5858010) · [author research page](https://www.daviddeming.com/research)

This work uses detailed online vacancy descriptions over a long modern period to measure the rate at which occupational skill requirements change and connects faster skill change to career earnings dynamics. The authors provide replication materials through the associated research resources. citeturn21search5turn21search8

**Relevance:** it provides a conceptual foundation for your eventual “what should I learn next?” layer: the *rate of skill change itself* can be economically/career relevant. The limitation is that its objective is labor economics at occupation scale, not a company-specific diff product.

**Acemoglu, Autor, Hazell & Restrepo, “Artificial Intelligence and Jobs: Evidence from Online Vacancies,” Journal of Labor Economics, 2022 — published.**  
[Publisher](https://www.journals.uchicago.edu/doi/abs/10.1086/718327)

The researchers use establishment-level data from a near-universe of U.S. online vacancies from 2010 onward. They find rapid growth in AI vacancies during 2010–2018 and, crucially, report that establishments adopting AI reduce non-AI hiring while **changing the skill requirements of their remaining postings**. citeturn25view9

**Relevance:** this is very close to one of your proposed outputs — identifying whether a particular employer is changing AI/ML expectations in otherwise comparable jobs. It confirms that employer-level textual skill shifts are a legitimate research quantity, although the underlying modern vacancy data are not an open reusable company-history corpus. citeturn25view9

### Skill extraction and normalization research

**Zhang et al., “SkillSpan: Hard and Soft Skill Extraction from English Job Postings,” NAACL 2022 — published.**  
[Code/data](https://github.com/kris927b/SkillSpan)

SkillSpan addresses the lack of high-quality span-level skill datasets. It provides 14.5K sentences and more than 12.5K expert-annotated spans from three sources, releasing two de-identified subsets. It evaluates BERT, domain-adapted models, span-oriented models and multitask variants, with the authors reporting that domain-adapted job-posting language models outperform their generic counterparts and that single-task models beat multitask models in their experiments. fileciteturn11file0

**Relevance:** use SkillSpan as a benchmark/gold-data source rather than creating your entire definition of “skill mention” from scratch.

**Nguyen et al., “Rethinking Skill Extraction in the Job Market Domain using Large Language Models,” NLP4HR 2024 — published workshop paper.**  
[ACL Anthology](https://aclanthology.org/2024.nlp4hr-1.3/) · [code](https://github.com/epfl-nlp/SCESC-LLM-skill-extraction)

The authors standardize six existing skill-extraction datasets and compare few-shot LLM approaches against supervised approaches. Their important result is nuanced: few-shot LLMs do **not** simply dominate conventional supervised models, although they are comparatively good at syntactically complicated skill mentions. citeturn25view6

That argues strongly against an “LLM extracts everything” architecture. A hybrid is more defensible: deterministic extraction where the signal is structured, trained span models for explicit skills, embedding/entity linking for normalization, and an LLM only for semantic cases where it actually adds value. citeturn25view5turn25view6

**Ao et al., “Skill requirements in job advertisements: comparison of skills extraction methods,” Information Processing & Management, 2023 — published.**  
[Publisher](https://www.sciencedirect.com/science/article/pii/S0306457322002862)

Using more than one million U.K. advertisements, this study compares top-down expert-created skill dictionaries with unsupervised topic modeling and tests their downstream explanatory usefulness. Its central methodological lesson is that pre-existing dictionaries and bottom-up text methods capture different information; a fixed taxonomy can miss emerging concepts. citeturn23search15turn23search8

For your project, that means **never discard the original phrase after taxonomy normalization**. Store both:

`"build agentic evaluation harnesses"` → raw phrase

and perhaps:

`AI / machine learning`, `software testing`, `evaluation` → normalized concepts.

Otherwise your taxonomy can literally normalize away the emerging language you are trying to detect.

### Recent public trend datasets and benchmarks

**Howell et al., “An economic geography dataset of U.S. skill specialization, relatedness, and complexity,” 2026 — preprint.**  
[arXiv](https://arxiv.org/abs/2606.09918) · [code/data](https://github.com/AntJam-Howell/skills-econ-geog-data)

This is a particularly useful recent reference because it documents the practical transformation of **433.6 million** Lightcast postings into a 2010–2024 longitudinal panel. It exposes skill-demand composition, specialization, relatedness, complexity and year-over-year dynamics, while releasing only aggregates rather than the licensed raw postings. citeturn25view10

Even more important for your evaluation design, the authors document a **2017–2018 vendor-methodology break** and caution about low-volume noise in granular measures. citeturn25view10 This is exactly the type of failure a career-intelligence product must expose rather than hiding behind a smooth chart: apparent skill change can originate in collection methodology.

**WorkRB, 2025–2026 — active benchmark/tooling project.**  
[GitHub](https://github.com/techwolf-ai/workrb)

WorkRB packages evaluation tasks for job normalization, job-title similarity, job-to-skill retrieval, skill extraction and skill normalization and provides lexical as well as neural baselines. Its included metrics—MAP, MRR, nDCG, recall and confidence intervals—make it useful scaffolding for objectively comparing the normalization/linking parts of your pipeline. fileciteturn13file0

### Engineering write-ups worth studying

The highest-value “blogs” here are not generic career articles; they are actual system descriptions:

**Eurostat WIH OJA methodology:** [https://cros.ec.europa.eu/wih/oja](https://cros.ec.europa.eu/wih/oja). It lays out source discovery, multiple acquisition modes, deduplication, ontology/ML extraction and quality validation, and explicitly documents representativeness and classification limitations. This is the best production-oriented architecture reference in the reviewed sources. citeturn25view1

**Nesta OJD Skills Extractor model card:** [https://nestauk.github.io/ojd_daps_skills/model_card/](https://nestauk.github.io/ojd_daps_skills/model_card/). Unlike a promotional page, it reports model stages, data size, precision/recall/F1, mapping behavior and known failure modes. citeturn25view5

**JobTech job-ad data documentation:** [https://data.jobtechdev.se/dataset/job-ads](https://data.jobtechdev.se/dataset/job-ads). It is especially useful for understanding the distinction between a current-search API, historical data, and an event stream of publication/update/unpublication operations. citeturn25view0

**Lightcast US Job Postings documentation:** [https://docs.lightcast.io/data-shares/docs/us-job-postings](https://docs.lightcast.io/data-shares/docs/us-job-postings). Its definitions of unique versus duplicate postings and its filter dimensions are useful schema/design references even when you do not have access to the full underlying product. citeturn25view2

## What the system actually needs to solve

The most important design decision is to stop treating “job matching” as a single problem. There are **three different identities**:

1. **Same posting, different version.**  
   Example: requisition `12345` is edited on May 2 to add “experience with LLM evaluation.”

2. **Same vacancy, duplicated publication.**  
   Example: one opening appears on the employer site, an aggregator, and two location-specific URLs.

3. **Different vacancies that are equivalent enough to compare.**  
   Example: “Software Engineer II — Payments” in November versus “Backend Engineer, Payments” in May.

The first is versioning. The second is deduplication. **The third is your product’s core inference problem.**

Conflating them will produce misleading trends. Eurostat explicitly dedicates a preprocessing stage to recognizing duplicate ads from text, location and metadata and merging them before statistics are calculated; Lightcast similarly exposes deduplicated “unique postings” separately from duplicate counts. citeturn25view1turn25view2

### Recommended role-equivalence model

I would not start with an end-to-end LLM asking “are these the same role?” Instead, use a blocked, interpretable matcher.

First canonicalize the employer. Use a stable employer identifier where the data source provides one; otherwise normalize legal suffixes and aliases and retain the original employer string.

Then assign a **broad occupation family** using a taxonomy such as ESCO. This gives you a guardrail against comparing “Data Scientist” with “Data Engineer” just because both mention Python. WorkRB already contains job-normalization and job-title-similarity tasks that can help benchmark candidate models for this stage. fileciteturn13file0

Next infer **seniority independently**: intern, junior, mid, senior, staff/principal, manager. Seniority should not be allowed to hide inside a single embedding because a shift from five years to eight years of required experience is precisely the kind of result you want to detect.

Also extract location/work modality and, where possible, product/subteam.

Finally calculate a semantic role score primarily from:

`normalized occupation + title + responsibility text + seniority + subteam`

rather than from the very skill requirements whose change you are measuring.

That last point is important: **using skill overlap heavily to decide that two jobs are equivalent biases your sample toward finding no skill change.** It is a form of target leakage. Role equivalence should be established mostly from occupation, organizational function and responsibility semantics; skills can be a weak supporting feature but not the main identity feature.

For a small sample, bipartite matching between old-window and new-window advertisements is sufficient. For a real product, a hierarchical role cluster is more defensible than forcing every advertisement into a one-to-one pair:

`Company → role family → level → subfunction → individual opening → posting versions`

Each pair or cluster should have a calibrated match score, not a binary label emitted without explanation.

### Recommended extraction architecture

The evidence strongly supports a **hybrid pipeline** rather than either “regex only” or “LLM only.” Nesta demonstrates that conventional NER plus embedding mapping works but has measurable false positives/negatives; SkillSpan shows the value of domain-adapted supervised models; the 2024 LLM comparison finds that few-shot LLMs are valuable on complex constructions without universally outperforming supervised methods. citeturn25view5turn25view6 fileciteturn11file0

I would extract five separate categories:

| Target | Recommended method | Important output |
|---|---|---|
| **Named tools / technologies** | alias dictionary + NER + embedding normalization | `Kubernetes`, `PyTorch`, `Snowflake`, `LangChain`; canonical ID plus original phrase |
| **General explicit skills** | SkillSpan/Nesta-like span model → ESCO/open taxonomy linking | raw span, normalized concept, extraction and mapping confidence |
| **AI/ML expectations** | high-recall AI/tool lexicon plus semantic classifier | distinction between “uses AI tools,” “builds ML systems,” “LLM experience preferred,” “owns AI strategy” |
| **Experience / qualifications** | deterministic parsers plus NER | years/range, degree, certification, domain experience, required versus preferred |
| **Responsibilities / implied skills** | sentence segmentation + embeddings; LLM second-stage interpretation | original responsibility sentence, semantic cluster, **explicitly marked inferred skill**, confidence |

Tabiya is particularly instructive because it already treats Occupation, Skill, Qualification, Experience and Domain as distinct entities before linking to an ontology. fileciteturn15file0

Do **not** merge explicit and implied skills into one field. A posting that literally says “Kubernetes required” is materially different evidence from a model inferring Kubernetes knowledge from “operate containerized services.”

Likewise, absence is ambiguous. An employer may stop mentioning a basic skill because it has become assumed rather than because it no longer matters. Eurostat/JRC work on online vacancies explicitly cautions that some capabilities can be underrepresented because employers treat them as self-evident. citeturn13search7 A credible UI should therefore say:

> “The mention rate fell from 62% to 28%”

rather than:

> “The company no longer needs this skill.”

unless you have a direct posting revision that actually removed the requirement.

### Separating real changes from rewriting and composition effects

Your central statistical problem is not extraction. It is the distinction:

\[
\text{observed change}
=
\text{true requirement change}
+
\text{posting rewrite}
+
\text{duplicate/repost effects}
+
\text{role-mix change}
+
\text{measurement error}.
\]

A practical system should therefore calculate at least **two trend estimates**.

**Paired-role change:** compare either actual versions of the same requisition or highly comparable old/new openings.

This answers: *“What changed inside otherwise equivalent jobs?”*

**Role-family prevalence change:** among all deduplicated advertisements in a fixed role family/level, calculate how often each requirement appears in each time window.

This answers: *“What does this company currently hire more often for?”*

Do not collapse these into one number. Suppose a company starts opening many more senior ML roles. “PyTorch mentions increased” may be absolutely true for the hiring mix while no existing software-engineering role changed. That is useful intelligence, but it is a different claim.

A third, more rigorous metric can standardize each period to a constant mix of seniority, location and sub-role. In conceptual terms:

\[
p_{t}^{standardized}(skill)
=
\sum_s w_{baseline,s}
\,p(skill\mid s,t)
\]

where \(s\) is a stratum such as seniority × location × sub-role. That gives you an estimate closer to “requirements changed” rather than “the company happened to hire different kinds of people.”

### What counts as a meaningful delta

A robust diff engine should collapse surface edits before surfacing changes.

“Experience with AWS” → “AWS experience” is not a change.

“Experience building ML models” → “experience deploying ML models to production” probably is.

“AI experience preferred” → “hands-on production LLM experience required” is a particularly important change because **modality and specificity changed**, not merely the noun.

“5+ years” → “7+ years” should be represented structurally as `minimum_experience +2 years`.

A responsibility sentence that was merely moved into another section is not a new responsibility.

A taxonomy alias update is not employer change.

A requirement seen in one posting out of one should not be reported as a “trend.”

For every surfaced trend, preserve the evidence lineage:

`source posting → version/date → source sentence → extracted span → normalized concept → role-match → aggregate trend`

That audit trail is the largest trust advantage your project can have over a generic LLM summary.

## Practical MVP and recommended data pipeline

### Start with an evidence experiment, not a multi-company platform

For the first MVP I would deliberately **let the available historical data select the three companies**, rather than choosing glamorous brands and discovering later that their history cannot be reconstructed.

Use **JobTech Sweden** because its official historical file goes back to 2006, its historical API covers unpublished ads from 2016 onward, JobStream provides live update events, and the relevant datasets are CC0. citeturn25view0

Choose one role family:

**Software Engineer / Software Developer — individual contributors only.**

Initially exclude managers, QA/testing specialists, SRE/DevOps, data scientists and dedicated ML engineers. That boundary makes it much easier to tell whether AI requirements are moving into ordinary software-engineering roles rather than merely observing more ML jobs.

Use two six-month windows:

**October 1, 2025–March 31, 2026**  
versus  
**April 1, 2026–September 30, 2026**

This avoids the incomplete current month of October 2026.

Query the historical dataset, normalize employers, and choose the **three direct employers with the largest usable number of target-role ads**, subject to a proposed minimum such as 10–15 deduplicated ads in each period. The “three companies” are therefore fixed after one data-coverage query, not predetermined by brand. That is a feature rather than a compromise: it prevents the feasibility test from being dominated by missing data.

A sensible MVP corpus is only **roughly 60–150 postings**. Human-review every role assignment and every high-impact reported delta. The goal is to determine whether the signal survives careful inspection, not to demonstrate scale.

For each company, produce one page with:

- total usable postings in each window;
- title/level/location composition;
- top explicit skills by mention rate;
- tools newly appearing or declining;
- AI/ML requirement categories;
- experience requirement distribution;
- newly appearing responsibility clusters;
- paired examples showing the exact old/new wording;
- confidence and sample size;
- a clear split between **within-equivalent-role change** and **change in the mix of jobs advertised**.

### Forward-looking U.S./global version

If U.S. historical microdata are not licensed, do **not** make the Wayback Machine the foundation of the product. The Internet Archive itself documents incomplete site coverage and one-page-only behavior for Save Page Now, while Common Crawl is a raw web archive rather than a vacancy database. citeturn25view3turn25view4

Instead, build a second MVP track that begins collecting history prospectively:

`Greenhouse / Lever / Ashby / direct career page`

→ retrieve all current postings once per day

→ persist immutable raw response

→ content hash each version

→ retain source posting/requisition ID

→ log first-seen, changed-at, last-seen and closed-at timestamps

→ never overwrite old text.

Within six months you then have something far more useful than an irregular archive: **true posting version histories**.

The defensible product version can combine:

**open historical demonstration** — JobTech;

**commercial validation if budget permits** — a Lightcast historical sample;

**your own proprietary longitudinal corpus** — snapshots going forward.

That approach treats data collection as an asset rather than an implementation detail.

### Recommended schema

At minimum, maintain three separate tables/entities rather than one flattened job row.

**Posting instance**

```text
posting_instance_id
source
source_posting_id
company_id
first_seen_at
last_seen_at
published_at
closed_at
canonical_url
location
employment_type
```

**Posting version**

```text
posting_instance_id
version_id
observed_at
raw_title
raw_description
content_hash
raw_source_payload
archive_reference
```

**Normalized analytical record**

```text
version_id
role_family
occupation_id
seniority
sub_role
work_modality
role_match_confidence

skill_mentions[]
tool_mentions[]
ai_expectations[]
experience_requirements[]
qualifications[]
responsibility_clusters[]
```

Each extracted object should retain its **raw source sentence, offsets, method/model version, taxonomy version and confidence**. That makes it possible to rerun the extractor later without rewriting history.

### Recommended processing pipeline

**Ingest and version.** Preserve the source payload before any normalization. For live feeds, interpret publication, modification and closure as separate events; JobTech JobStream is a concrete example of a source that exposes precisely such changes. citeturn25view0

**Identify exact and cross-source duplicates.** First use stable source IDs. Next use canonical URL/requisition identifiers. Only then use fuzzy similarity based on company, title, location and body text. The distinction between unique and duplicate postings is already considered important enough to be a first-class metric in Lightcast and a dedicated preprocessing step in Eurostat WIH. citeturn25view1turn25view2

**Normalize roles.** Use deterministic title cleanup and seniority rules, then occupation classification, then a title/responsibility embedding. Benchmark alternatives with WorkRB-style normalization tasks. fileciteturn13file0

**Segment description sections.** Preserve requirements, responsibilities, qualifications, benefits and company boilerplate separately. Boilerplate changes should never become role trends.

**Extract explicit requirements.** Start with the simplest trustworthy combination: structured regex for years/degrees, a SkillSpan/Nesta-class model for spans, and ESCO mapping with embedding similarity. Nesta’s actual implementation is a strong starting point but its reported extraction performance shows why manual evaluation on your own corpus remains mandatory. citeturn25view5

**Add an LLM second stage only after the baseline works.** Use it for requirement modality, semantic responsibility changes, implied skill interpretation and explaining deltas—not as the sole source of factual skill presence. The cross-dataset 2024 evaluation does not support the assumption that a few-shot LLM universally beats conventional supervised extraction. citeturn25view6

**Compute temporal metrics.** Keep both exact paired deltas and window-level prevalence. For a skill \(k\),

\[
\Delta_k =
\frac{\#\text{deduplicated target-role postings mentioning }k}
     {\#\text{deduplicated target-role postings}}
\Bigg|_{\text{new}}
-
\frac{\#\text{postings mentioning }k}
     {\#\text{postings}}
\Bigg|_{\text{old}}.
\]

Report the numerator and denominator, not just the percentage-point difference.

Then bootstrap at the **posting level**, rather than at the individual mention level, to get uncertainty intervals. Also calculate results under alternate role-matching and deduplication thresholds. A trend that appears only under one arbitrary threshold should be labeled fragile.

**Generate evidence-first career intelligence.** A job seeker should see something like:

> **Kubernetes: emerging, medium confidence.** Mentioned in 3/21 comparable backend postings in Oct–Mar versus 10/24 in Apr–Sep. Seven of the ten recent mentions were marked “required.” The change persists when senior roles are excluded. Evidence: five representative posting excerpts.

That is far more defensible than “Learn Kubernetes because AI says so.”

A future learning-priority score could combine:

\[
\text{priority}
=
\text{growth}
\times
\text{persistence}
\times
\text{role specificity}
\times
\text{cross-employer evidence}
\times
\text{user skill gap}.
\]

That formula is a **proposed product heuristic**, not an established research metric. It should only be introduced after the underlying trend measurements are validated.

## Evaluation, first experiments, and go/no-go criteria

### Evaluation should be layered

Do not evaluate the product only by asking whether the final dashboard “looks reasonable.” Every inference layer can fail independently.

**Extraction evaluation.** Create an in-domain gold set of perhaps 200–300 carefully selected posting passages containing tools, hard skills, soft skills, AI terms, experience, qualifications and responsibilities. Label exact spans, normalized concept, required/preferred status and experience quantities. Report exact-span and partial-span precision/recall/F1 plus taxonomy-link accuracy. SkillSpan gives you an external annotated reference set, while Nesta’s model card is a useful precedent for separately reporting span extraction, multi-skill splitting and taxonomy mapping instead of hiding them in one metric. citeturn25view5 fileciteturn11file0

For the user-facing product, **precision matters more than recall**. Missing one emerging skill is unfortunate; confidently telling thousands of job seekers that a company has newly started requiring a skill when it has not is much worse.

**Role-equivalence evaluation.** Human-label candidate pairs as:

`equivalent role`, `same family but materially different level/sub-role`, or `not equivalent`.

Evaluate pairwise precision/recall and calibration. Compare at least a title-only baseline, title embedding, occupation+title, and occupation+title+responsibility model. WorkRB’s job-normalization/title-similarity tasks give you reusable benchmark infrastructure, although your temporal-equivalence labels will still need to be domain-specific. fileciteturn13file0

**Posting-identity evaluation.** Label pairs as:

`same version`,  
`same vacancy, edited`,  
`same vacancy, cross-channel duplicate`,  
`repost/new requisition`,  
`different vacancy`.

This is a more useful taxonomy than a generic “duplicate/not duplicate” classifier because a rewritten vacancy is precisely the evidence you want to keep.

**Trend-reliability evaluation.** Recompute every reported trend under:

- no deduplication versus deduplication;
- broad versus strict role matching;
- raw cohort composition versus standardized composition;
- exclusion of small locations;
- exclusion of senior roles;
- paired advertisements only versus all role-family advertisements.

Howell et al.’s explicit warning about vendor-methodology breaks and low-volume noise is a reminder that longitudinal metrics need stability tests, not just extraction benchmarks. citeturn25view10

**Job-seeker usefulness evaluation.** Only after the factual pipeline works, compare two outputs in a blinded user test:

“most frequently requested skills right now”

versus

“requirements that are newly rising and persistent, with evidence.”

Measure whether people can identify a reasonable learning priority, whether they understand the uncertainty, and whether the historical evidence changes their decision. That directly tests the distinctive value proposition rather than asking users whether a colorful dashboard is appealing.

### The first three feasibility experiments

#### Data and temporal-identity experiment

Use the JobTech historical corpus for one software-engineering role family and the three highest-volume eligible employers across the two six-month windows. citeturn25view0

Measure:

- postings available per employer/window;
- percentage that can be cleanly assigned to the target role family;
- exact duplicates and near duplicates;
- title/level/location composition shift;
- how often stable IDs or metadata permit stronger identity claims;
- number of genuinely comparable old/new openings.

**Proposed go criterion:** enough volume to obtain at least dozens of comparable ads and high human agreement on role-family membership.

**Failure interpretation:** if the project fails here, do not build an LLM layer. The data—not the model—is the bottleneck.

#### Requirement-delta extraction experiment

Take roughly 100 high-confidence old/new role pairs or role-cluster comparisons and manually label the actual differences.

Compare three systems:

**Lexical/rule baseline:** dictionaries, regex, aliases.

**Open NLP baseline:** Nesta/SkillSpan-style explicit span extraction plus ESCO mapping. citeturn25view5 fileciteturn11file0

**Hybrid:** the same baseline plus an LLM for modality, paraphrased responsibilities and implied competencies.

Evaluate *delta precision*, not merely extraction F1:

> Of the changes shown to a user, what fraction are actually meaningful requirement changes?

This matters because two systems can have similar entity-extraction F1 while behaving very differently when their errors are differenced across time.

A reasonable proposed threshold for a public-facing MVP is **at least roughly 85% precision on the “material changes” you choose to surface**, with lower-confidence observations hidden or explicitly qualified. That threshold is a product criterion, not a literature-established standard.

#### Trend-stability and false-discovery experiment

This is the most important experiment.

Compute apparent requirement changes under:

1. raw advertisements;
2. deduplicated advertisements;
3. narrowly role-matched advertisements;
4. mix-standardized advertisements.

Then perform a **negative control**: randomly split postings from the same stable time interval into two pseudo-periods and run the entire trend detector.

A trustworthy system should find relatively few high-confidence “emerging skills” in the random split. If it routinely discovers dramatic trends when no time change was introduced, the pipeline is measuring sampling noise, duplicate structure or matching artifacts.

Also inspect whether top trends disappear when duplicate postings are removed. If they do, you have discovered distribution mechanics, not career intelligence. The emphasis that both Lightcast and Eurostat put on deduplication supports treating this as a foundational reliability test rather than preprocessing trivia. citeturn25view1turn25view2

### Reasons to continue, narrow, or stop

**Continue** because the central phenomenon is real. Peer-reviewed work shows changes occurring within narrow job titles and within establishments, including changes associated with AI adoption. Large commercial and statistical systems already invest heavily in historical posting/skill analysis, validating demand for the broader category. Open-source skill extraction and normalization components are mature enough that you do not need to invent the entire NLP stack. citeturn25view7turn25view9turn25view2turn25view5

**Continue** because there is a credible product gap. The reviewed systems split into three camps: historical labor-market data products, extraction/normalization components, and academic longitudinal studies. I did not find an open implementation that connects individual company-role histories to evidence-backed, uncertainty-aware career learning recommendations.

**Narrow** the idea to **one role family and explicit requirements first**. Do not initially promise to infer every latent skill from responsibilities. Named tools, explicit skills, AI/ML expectations, years of experience and required/preferred modality already provide a substantial useful product.

**Narrow** the historical claim geographically/data-wise. For the first proof, use JobTech because the history and CC0 status are explicit. For U.S. company-level history, either acquire a legitimate historical source such as Lightcast or describe Wayback/Common Crawl reconstruction honestly as partial. JobTech provides a clean research environment; Common Crawl and Wayback provide opportunistic evidence, not a complete denominator. citeturn25view0turn25view3turn25view4

**Start collecting forward immediately.** Greenhouse, Lever, Ashby and accessible employer pages make it feasible to create your own versioned corpus of current postings. Six to twelve months of consistent first-party observation is ultimately more defensible for a product than trying indefinitely to reconstruct every employer’s missing past from web archives.

**Stop or pivot** if careful human evaluation shows that equivalent-role matching cannot reach high precision, because every downstream trend would then be contaminated by role mix.

**Stop or pivot** if your headline “changes” routinely vanish after deduplication or seniority/location standardization.

**Stop or pivot** if the majority of deltas are cosmetic rewrites rather than changes a job seeker could reasonably act on.

**Stop trying to make historical company-level U.S. claims** if the only available evidence for an employer is sporadic Wayback snapshots. In that case, the useful product is **“we began tracking this company on [date]; here is what has changed since”**, supplemented by carefully labeled archive evidence. That is still valuable and is much more trustworthy than pretending sparse captures form a complete historical sample. Internet Archive itself cautions that archived coverage can be absent or incomplete. citeturn25view4

The strongest version of the project is therefore:

> **A version-control system for job requirements, normalized to comparable company-role families, that separates actual requirement shifts from reposting and hiring-mix effects and shows every career recommendation with the postings and sentences that support it.**

That is materially different from another job scraper, resume matcher, or “top skills this month” dashboard—and the existing data, research, extraction libraries and evaluation frameworks are sufficient to test whether it works before committing to a platform.