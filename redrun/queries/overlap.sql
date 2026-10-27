-- Cross-source overlap over runs, changes, log_errors, dependencies.
-- One flat SELECT with no WITH clause: Zetaris Lightning 2.4.x rejects a
-- leading WITH (LightningDdlParseException: mismatched input 'with').
-- STRING casts instead of VARCHAR so Spark and DuckDB both accept it.
SELECT link_type, log_evidence_id, change_evidence_id, score_hint, detail
FROM (
    SELECT
        'dependency_match' AS link_type,
        le.evidence_id AS log_evidence_id,
        d.evidence_id AS change_evidence_id,
        CAST(0.85 AS DOUBLE) AS score_hint,
        concat(
            'log error names package ', d.package,
            ' which changed ', coalesce(d.old_version, '?'), ' -> ', coalesce(d.new_version, '?'),
            ' and that release landed between the green and failed runs'
        ) AS detail
    FROM log_errors le
    JOIN dependencies d ON le.package_hint = d.package
    WHERE le.package_hint IS NOT NULL
      AND d.released_between_runs = TRUE
    UNION ALL
    SELECT
        'file_match',
        le.evidence_id,
        c.evidence_id,
        CAST(0.80 AS DOUBLE),
        concat('failing log line names ', le.file_hint, ' which changed in ', c.path)
    FROM log_errors le
    JOIN changes c ON le.file_hint = c.path
    WHERE le.file_hint IS NOT NULL
    UNION ALL
    SELECT
        'secret_or_config_match',
        le.evidence_id,
        c.evidence_id,
        CAST(0.75 AS DOUBLE),
        concat('auth or secret failure alongside changed ', c.path)
    FROM log_errors le
    CROSS JOIN changes c
    WHERE (
            lower(le.message) LIKE '%secret%'
            OR lower(le.message) LIKE '%401%'
            OR lower(le.message) LIKE '%403%'
            OR lower(le.message) LIKE '%unauthoriz%'
            OR lower(le.error_class) LIKE '%secret%'
        )
      AND (
            lower(c.path) LIKE '%/.github/workflows/%'
            OR lower(c.path) LIKE '%.yml'
            OR lower(c.path) LIKE '%.yaml'
        )
    UNION ALL
    SELECT
        'config_match',
        CAST(NULL AS STRING),
        CAST(NULL AS STRING),
        CAST(0.60 AS DOUBLE),
        concat(
            'runner image changed ', coalesce(g.runner_image, 'none'),
            ' -> ', coalesce(f.runner_image, 'none'), ' between the green and failed runs'
        )
    FROM runs g
    JOIN runs f ON g.role = 'green' AND f.role = 'failed'
    WHERE coalesce(g.runner_image, '') <> coalesce(f.runner_image, '')
    UNION ALL
    SELECT
        'config_match',
        CAST(NULL AS STRING),
        CAST(NULL AS STRING),
        CAST(0.55 AS DOUBLE),
        'workflow file changed between the green and failed runs'
    FROM runs g
    JOIN runs f ON g.role = 'green' AND f.role = 'failed'
    WHERE coalesce(g.workflow_file_sha, '') <> coalesce(f.workflow_file_sha, '')
) all_links
ORDER BY score_hint DESC, link_type, log_evidence_id
