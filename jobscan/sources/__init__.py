from . import greenhouse, lever, workday, rss, jobspy_src  # noqa: F401

REGISTRY = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "workday": workday.fetch,
    "rss": rss.fetch,
    "jobspy": jobspy_src.fetch,
}
