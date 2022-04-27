"""MAL script for sorting anime by last finished."""
from datetime import datetime
import json
import os
from pathlib import Path
import re
import secrets

import pandas as pd
import requests
from aiohttp import web
from aiohttp.web_runner import GracefulExit
from bs4 import BeautifulSoup


class MyAnimeList:
    """MyAnimeList API."""

    def __init__(self):
        """Init MyAnimeList class."""
        self.CLIENT_ID = os.getenv("MALID")
        self.CLIENT_SECRET = os.getenv("MALSECRET")
        self.CODE_CHALLENGE = secrets.token_urlsafe(80)
        self.code = None
        self.token = None
        if "token.json" in os.listdir() and os.path.isfile("token.json"):
            with open("token.json", "r") as file:
                self.token = json.load(file)
        else:
            self._authorize()
        self.alist = mal.get_animelist()

    async def _auth_url(self):
        """Print the URL needed to authorise your application."""
        params = f"&client_id={self.CLIENT_ID}&code_challenge={self.CODE_CHALLENGE}"
        url = f"https://myanimelist.net/v1/oauth2/authorize?response_type=code{params}"
        print(f"Authorise MyAnimeList account by clicking URL: {url}\n")

    async def _handle(self, request: web.Request):
        """Handle request from MAL OAuth2 authorisation."""
        if "code" in request.query:
            self.code = request.query["code"]
        else:
            raise web.HTTPBadRequest(reason="No OAUTH code was returned by MAL.")
        raise GracefulExit()

    def _get_access_token(self, code):
        """Get access token using code."""
        url = "https://myanimelist.net/v1/oauth2/token"
        data = {
            "client_id": self.CLIENT_ID,
            "client_secret": self.CLIENT_SECRET,
            "code": code,
            "code_verifier": self.CODE_CHALLENGE,
            "grant_type": "authorization_code",
        }
        response = requests.post(url, data)
        response.raise_for_status()  # Check whether the requests contains errors
        token = response.json()
        response.close()
        print("Token generated successfully!")
        with open("token.json", "w") as file:
            json.dump(token, file, indent=4)
            print('Token saved in "token.json"')
        self.token = token

    def _authorize(self):
        """Get access token if not present."""
        app = web.Application()
        app.add_routes([web.get("/callback", self._handle)])
        app.on_startup.append(lambda app: self._auth_url())
        web.run_app(app, port=9712)
        self._get_access_token(self.code)
        return self

    def get(self, url):
        """Get data from MAL."""
        headers = {"Authorization": f"Bearer {self.token['access_token']}"}
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response

    def patch(self, url, data):
        """Put data to MAL."""
        headers = {"Authorization": f"Bearer {self.token['access_token']}"}
        response = requests.patch(url, data=data, headers=headers)
        return response

    def patch_anime(self, anime_id, data):
        """Update anime data."""
        return self.patch(
            f"https://api.myanimelist.net/v2/anime/{anime_id}/my_list_status", data,
        )

    def get_stats(self):
        """Get user stats."""
        url = "https://api.myanimelist.net/v2/users/@me?fields=anime_statistics"
        return self.get(url).json()

    def get_number_of_anime(self):
        """Get number of anime."""
        stats = self.get_stats()
        return stats["anime_statistics"]["num_items"]

    def get_animelist(self) -> pd.DataFrame:
        """Get anime list from MAL as dataframe."""
        num_anime = self.get_number_of_anime()
        animelist = []
        params = "?fields=list_status&limit=100&nsfw=true"
        url = f"https://api.myanimelist.net/v2/users/@me/animelist{params}"
        while True:
            response = mal.get(url).json()
            animelist += response["data"]
            print(f"{len(animelist)}/{num_anime}")
            if "next" in response["paging"]:
                url = response["paging"]["next"]
            else:
                break
        flatten_list = [{**item["node"], **item["list_status"]} for item in animelist]
        animelist = pd.DataFrame.from_records(flatten_list)
        return animelist

    def get_anime_update_history(self, anime_id: int):
        """Get anime update history."""
        html = self.get(
            f"https://myanimelist.net/ajaxtb.php?keepThis=true&detailedaid={anime_id}"
        ).text
        soup = BeautifulSoup(html, "html.parser")
        anime_title = soup.find("div", {"class": "normal_header"}).text
        updates = soup.find_all("div", {"class": "spaceit_pad"})
        anime_title = anime_title.replace(" Episode Details", "")
        updates = [i.text.replace(" Remove", "") for i in updates]
        return {"anime_title": anime_title, "anime_id": anime_id, "updates": updates}

    def format_title(title: str) -> str:
        """Format title into filename safe string."""
        return re.sub(r"[^A-Za-z0-9\ ]+", "", title).replace(" ", "_")

    def cache_anime_update_history(self, anime_id: int, outfile: str):
        """Call get anime update history with file system caching."""
        if not os.path.exists(outfile):
            with open(outfile, "w") as f:
                info = self.get_anime_update_history(anime_id)
                json.dump(info, f, indent=4)
                print(f"Saved to {outfile}")

    def cache_completed_histories(self):
        """Cache completed histories."""
        completed = self.alist[self.alist.status == "completed"]
        for anime_id, title in completed[["id", "title"]].values.tolist():
            title = MyAnimeList.format_title(title)
            Path("cache").mkdir(exist_ok=True)
            outfile = f"cache/{anime_id}.json"
            mal.cache_anime_update_history(anime_id, outfile)
        print("Cached watch histories!")

    def dmY_to_Ymd(self, date: str):
        """Convert 08/10/2018 to 2018-10-08."""
        return datetime.strptime(date, "%m/%d/%Y").strftime("%Y-%m-%d")

    def get_start_finish_dates(self):
        """Return list of records with anime_id, start_date, and finish_date."""
        d = "cache/"
        ret = []
        for fname in os.listdir("cache/"):
            with open(os.path.join(d, fname), "r") as f:
                data = json.load(f)
                ret.append(
                    {
                        "id": data["anime_id"],
                        "start_date": self.dmY_to_Ymd(data["updates"][-1].split()[4]),
                        "finish_date": self.dmY_to_Ymd(data["updates"][0].split()[4]),
                    }
                )
        return ret

    def get_reorder_df(self):
        """Get reorder dataframe."""
        df = pd.DataFrame.from_records(self.get_start_finish_dates())
        df["sort"] = pd.to_datetime(df.finish_date)
        df = df.sort_values("sort")
        df = df.merge(self.alist[["id", "score", "title"]], on="id")
        return df

    def reorder_by_finished_date(self):
        """Reorder completed by finish date.

        Also adds start/finish date based on update history.
        Does not account for rewatch; sets finish date as most recent
        finish date.
        """
        for i in mal.get_reorder_df().itertuples():
            anime_id = i.id
            metadata = {
                "score": i.score,
                "start_date": i.start_date,
                "finish_date": i.finish_date,
            }
            print(mal.patch_anime(anime_id, metadata).text)


if __name__ == "__main__":
    mal = MyAnimeList()
    mal.cache_completed_histories()
    mal.reorder_by_finished_date()
