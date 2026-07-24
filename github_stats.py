#!/usr/bin/python3

import asyncio
import json
import os
from typing import Dict, List, Optional, Set, Union

import aiohttp
import requests


###############################################################################
# Main Classes
###############################################################################

class Queries(object):
    """
    Class with functions to query the GitHub GraphQL (v4) API and the REST (v3)
    API. Also includes functions to dynamically generate GraphQL queries.
    """

    def __init__(self, username: str, access_token: str,
                 session: aiohttp.ClientSession, max_connections: int = 10):
        self.username = username
        self.access_token = access_token
        self.session = session
        self.semaphore = asyncio.Semaphore(max_connections)

    async def query(self, generated_query: str) -> Dict:
        """
        Make a request to the GraphQL API using the authentication token from
        the environment
        :param generated_query: string query to be sent to the API
        :return: decoded GraphQL JSON output
        """
        headers = {
            "Authorization": f"Bearer {self.access_token}",
        }
        try:
            async with self.semaphore:
                r = await self.session.post("https://api.github.com/graphql",
                                            headers=headers,
                                            json={"query": generated_query})
            return await r.json()
        except:
            print("aiohttp failed for GraphQL query")
            # Fall back on non-async requests
            async with self.semaphore:
                r = requests.post("https://api.github.com/graphql",
                                  headers=headers,
                                  json={"query": generated_query})
                return r.json()

    async def query_rest(self, path: str, params: Optional[Dict] = None,
                         max_202_retries: int = 3) -> Optional[Union[Dict, List]]:
        """
        Make a request to the REST API
        :param path: API path to query
        :param params: Query parameters to be passed to the API
        :return: deserialized REST JSON output
        """

        request_path = path[1:] if path.startswith("/") else path

        for attempt in range(max_202_retries):
            headers = {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.access_token}",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            if params is None:
                params = dict()
            try:
                async with self.semaphore:
                    r = await self.session.get(f"https://api.github.com/{request_path}",
                                               headers=headers,
                                               params=tuple(params.items()))
                if r.status == 202:
                    # print(f"{path} returned 202. Retrying...")
                    print(f"{request_path} returned 202. Retrying...")
                    retry_after = r.headers.get("Retry-After")
                    wait_seconds = int(retry_after) if retry_after and retry_after.isdigit() else min(30, 2 ** attempt)
                    await asyncio.sleep(wait_seconds)
                    continue

                result = await r.json()
                if result is not None:
                    return result
            except:
                print("aiohttp failed for rest query")
                # Fall back on non-async requests
                async with self.semaphore:
                    r = requests.get(f"https://api.github.com/{request_path}",
                                     headers=headers,
                                     params=tuple(params.items()))
                    if r.status_code == 202:
                        print(f"{request_path} returned 202. Retrying...")
                        retry_after = r.headers.get("Retry-After")
                        wait_seconds = int(retry_after) if retry_after and retry_after.isdigit() else min(30, 2 ** attempt)
                        await asyncio.sleep(wait_seconds)
                        continue
                    elif r.status_code == 200:
                        return r.json()
        # print(f"There were too many 202s. Data for {path} will be incomplete.")
        print(f"There were too many 202s for {request_path}. Data for this repository will be incomplete.")
        return None

    @staticmethod
    def repos_overview(contrib_cursor: Optional[str] = None,
                       owned_cursor: Optional[str] = None) -> str:
        """
        :return: GraphQL query with overview of user repositories
        """
        return f"""{{
  viewer {{
    login,
    name,
    repositories(
        first: 100,
        orderBy: {{
            field: UPDATED_AT,
            direction: DESC
        }},
        isFork: false,
        after: {"null" if owned_cursor is None else '"'+ owned_cursor +'"'}
    ) {{
      pageInfo {{
        hasNextPage
        endCursor
      }}
      nodes {{
        nameWithOwner
        stargazers {{
          totalCount
        }}
        forkCount
        languages(first: 10, orderBy: {{field: SIZE, direction: DESC}}) {{
          edges {{
            size
            node {{
              name
              color
            }}
          }}
        }}
      }}
    }}
    repositoriesContributedTo(
        first: 100,
        includeUserRepositories: false,
        orderBy: {{
            field: UPDATED_AT,
            direction: DESC
        }},
        contributionTypes: [
            COMMIT,
            PULL_REQUEST,
            REPOSITORY,
            PULL_REQUEST_REVIEW
        ]
        after: {"null" if contrib_cursor is None else '"'+ contrib_cursor +'"'}
    ) {{
      pageInfo {{
        hasNextPage
        endCursor
      }}
      nodes {{
        nameWithOwner
        stargazers {{
          totalCount
        }}
        forkCount
        languages(first: 10, orderBy: {{field: SIZE, direction: DESC}}) {{
          edges {{
            size
            node {{
              name
              color
            }}
          }}
        }}
      }}
    }}
  }}
}}
"""

    @staticmethod
    def contrib_years() -> str:
        """
        :return: GraphQL query to get all years the user has been a contributor
        """
        return """
query {
  viewer {
    contributionsCollection {
      contributionYears
    }
  }
}
"""

    @staticmethod
    def contribs_by_year(year: str) -> str:
        """
        :param year: year to query for
        :return: portion of a GraphQL query with desired info for a given year
        """
        return f"""
    year{year}: contributionsCollection(
        from: "{year}-01-01T00:00:00Z",
        to: "{int(year) + 1}-01-01T00:00:00Z"
    ) {{
      contributionCalendar {{
        totalContributions
      }}
    }}
"""

    @classmethod
    def all_contribs(cls, years: List[str]) -> str:
        """
        :param years: list of years to get contributions for
        :return: query to retrieve contribution information for all user years
        """
        by_years = "\n".join(map(cls.contribs_by_year, years))
        return f"""
query {{
  viewer {{
    {by_years}
  }}
}}
"""
    @staticmethod
    def search_count(query: str) -> str:
        """
        :param query: GitHub search query string
        :return: query to retrieve the total number of matching issues/PRs
        """
        return f"""{{
    search(type: ISSUE, query: {json.dumps(query)}, first: 1) {{
        issueCount
    }}
}}
"""


class Stats(object):
    """
    Retrieve and store statistics about GitHub usage.
    """
    def __init__(self, username: str, access_token: str,
                 session: aiohttp.ClientSession,
                 exclude_repos: Optional[Set] = None,
                 exclude_langs: Optional[Set] = None,
                 consider_forked_repos: bool = False):
        self.username = username
        self._exclude_repos = set() if exclude_repos is None else exclude_repos
        self._exclude_langs = set() if exclude_langs is None else exclude_langs
        self._consider_forked_repos = consider_forked_repos
        self.queries = Queries(username, access_token, session)

        self._name = None
        self._stargazers = None
        self._forks = None
        self._total_contributions = None
        self._merged_prs = None
        self._issues_raised = None
        self._languages = None
        self._repos = None
        

    async def to_str(self) -> str:
        """
        :return: summary of all available statistics
        """
        languages = await self.languages_proportional
        formatted_languages = "\n  - ".join(
            [f"{k}: {v:0.4f}%" for k, v in languages.items()]
        )
        return f"""Name: {await self.name}
Stargazers: {await self.stargazers:,}
Forks: {await self.forks:,}
All-time contributions: {await self.total_contributions:,}
PRs merged: {await self.merged_prs:,}
Issues raised: {await self.issues_raised:,}
Repositories with contributions: {len(await self.all_repos)}
Languages:
  - {formatted_languages}"""

    async def get_stats(self) -> None:
        """
        Get lots of summary statistics using one big query. Sets many attributes
        """
        self._stargazers = 0
        self._forks = 0
        self._languages = dict()
        self._repos = set()
        self._ignored_repos = set()
        
        next_owned = None
        next_contrib = None
        while True:
            raw_results = await self.queries.query(
                Queries.repos_overview(owned_cursor=next_owned,
                                       contrib_cursor=next_contrib)
            )
            raw_results = raw_results if raw_results is not None else {}

            self._name = (raw_results
                          .get("data", {})
                          .get("viewer", {})
                          .get("name", None))
            if self._name is None:
                self._name = (raw_results
                              .get("data", {})
                              .get("viewer", {})
                              .get("login", "No Name"))

            contrib_repos = (raw_results
                             .get("data", {})
                             .get("viewer", {})
                             .get("repositoriesContributedTo", {}))
            owned_repos = (raw_results
                           .get("data", {})
                           .get("viewer", {})
                           .get("repositories", {}))
            
            repos = owned_repos.get("nodes", [])
            if self._consider_forked_repos:
                repos += contrib_repos.get("nodes", [])
            else:
                for repo in contrib_repos.get("nodes", []):
                    if not repo:
                        continue
                    name = repo.get("nameWithOwner")
                    if name in self._ignored_repos or name in self._exclude_repos:
                        continue
                    self._ignored_repos.add(name)

            for repo in repos:
                if not repo:
                    continue
                name = repo.get("nameWithOwner")
                if name in self._repos or name in self._exclude_repos:
                    continue
                self._repos.add(name)
                self._stargazers += repo.get("stargazers").get("totalCount", 0)
                self._forks += repo.get("forkCount", 0)

                for lang in repo.get("languages", {}).get("edges", []):
                    name = lang.get("node", {}).get("name", "Other")
                    languages = await self.languages
                    if name in self._exclude_langs: continue
                    if name in languages:
                        languages[name]["size"] += lang.get("size", 0)
                        languages[name]["occurrences"] += 1
                    else:
                        languages[name] = {
                            "size": lang.get("size", 0),
                            "occurrences": 1,
                            "color": lang.get("node", {}).get("color")
                        }

            if owned_repos.get("pageInfo", {}).get("hasNextPage", False) or \
                    contrib_repos.get("pageInfo", {}).get("hasNextPage", False):
                next_owned = (owned_repos
                              .get("pageInfo", {})
                              .get("endCursor", next_owned))
                next_contrib = (contrib_repos
                                .get("pageInfo", {})
                                .get("endCursor", next_contrib))
            else:
                break

        # TODO: Improve languages to scale by number of contributions to
        #       specific filetypes
        langs_total = sum([v.get("size", 0) for v in self._languages.values()])
        for k, v in self._languages.items():
            v["prop"] = 100 * (v.get("size", 0) / langs_total)

    @property
    async def name(self) -> str:
        """
        :return: GitHub user's name (e.g., Jacob Strieb)
        """
        if self._name is not None:
            return self._name
        await self.get_stats()
        assert(self._name is not None)
        return self._name

    @property
    async def stargazers(self) -> int:
        """
        :return: total number of stargazers on user's repos
        """
        if self._stargazers is not None:
            return self._stargazers
        await self.get_stats()
        assert(self._stargazers is not None)
        return self._stargazers

    @property
    async def forks(self) -> int:
        """
        :return: total number of forks on user's repos
        """
        if self._forks is not None:
            return self._forks
        await self.get_stats()
        assert(self._forks is not None)
        return self._forks

    @property
    async def languages(self) -> Dict:
        """
        :return: summary of languages used by the user
        """
        if self._languages is not None:
            return self._languages
        await self.get_stats()
        assert(self._languages is not None)
        return self._languages

    @property
    async def languages_proportional(self) -> Dict:
        """
        :return: summary of languages used by the user, with proportional usage
        """
        if self._languages is None:
            await self.get_stats()
            assert(self._languages is not None)

        return {k: v.get("prop", 0) for (k, v) in self._languages.items()}

    @property
    async def repos(self) -> List[str]:
        """
        :return: list of names of user's repos
        """
        if self._repos is not None:
            return self._repos
        await self.get_stats()
        assert(self._repos is not None)
        return self._repos
    
    @property
    async def all_repos(self) -> List[str]:
        """
        :return: list of names of user's repos with contributed repos included
                irrespective of whether the ignore flag is set or not
        """
        if self._repos is not None and self._ignored_repos is not None:
            return self._repos | self._ignored_repos
        await self.get_stats()
        assert(self._repos is not None)
        assert(self._ignored_repos is not None)
        return self._repos | self._ignored_repos

    @property
    async def total_contributions(self) -> int:
        """
        :return: count of user's total contributions as defined by GitHub
        """
        if self._total_contributions is not None:
            return self._total_contributions

        self._total_contributions = 0
        years = (await self.queries.query(Queries.contrib_years())) \
            .get("data", {}) \
            .get("viewer", {}) \
            .get("contributionsCollection", {}) \
            .get("contributionYears", [])
        by_year = (await self.queries.query(Queries.all_contribs(years))) \
            .get("data", {}) \
            .get("viewer", {}).values()
        for year in by_year:
            self._total_contributions += year \
                .get("contributionCalendar", {}) \
                .get("totalContributions", 0)
        return self._total_contributions

    @property
    async def merged_prs(self) -> int:
        """
        :return: count of merged pull requests authored by the user
        """
        if self._merged_prs is not None:
            return self._merged_prs

        result = await self.queries.query(
            Queries.search_count(f"author:{self.username} is:pr is:merged")
        )
        self._merged_prs = (
            result
            .get("data", {})
            .get("search", {})
            .get("issueCount", 0)
        )
        return self._merged_prs

    @property
    async def issues_raised(self) -> int:
        """
        :return: count of issues authored by the user
        """
        if self._issues_raised is not None:
            return self._issues_raised

        result = await self.queries.query(
            Queries.search_count(f"author:{self.username} is:issue")
        )
        self._issues_raised = (
            result
            .get("data", {})
            .get("search", {})
            .get("issueCount", 0)
        )
        return self._issues_raised

###############################################################################
# Main Function
###############################################################################

async def main() -> None:
    """
    Used mostly for testing; this module is not usually run standalone
    """
    access_token = os.getenv("ACCESS_TOKEN")
    user = os.getenv("GITHUB_ACTOR")
    async with aiohttp.ClientSession() as session:
        s = Stats(user, access_token, session)
        print(await s.to_str())


if __name__ == "__main__":
    asyncio.run(main())
