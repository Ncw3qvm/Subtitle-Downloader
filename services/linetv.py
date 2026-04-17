#!/usr/bin/python3
# coding: utf-8

"""
This module is to download subtitle from LineTV.
"""

import os
import re
import shutil
import sys
from time import localtime, strftime
from urllib.parse import quote, urlparse

import orjson

from configs.config import credentials
from services.baseservice import BaseService
from utils.helper import check_url_exist, get_language_code, get_locale
from utils.io import download_files, rename_filename
from utils.subtitle import convert_subtitle


class LineTV(BaseService):
    """
    Service code for Line TV streaming service (https://www.linetv.tw/).
    """

    def __init__(self, args):
        super().__init__(args)
        self._ = get_locale(__name__, self.locale)

    def _get_drama_id(self, url):
        """Robustly extract drama ID from URL path."""
        try:
            path = urlparse(url).path
            match = re.search(r'^/drama/(\d+)(?=/|$)', path)
            if match:
                return match.group(1)
        except Exception:
            pass
        return None

    def _get_access_token(self) -> str:
        """Read token from config first, then cookie."""
        access_token = ''

        if self.platform in credentials and 'access_token' in credentials[self.platform]:
            access_token = credentials[self.platform]['access_token']

        if not access_token and 'LINETV' in credentials and 'access_token' in credentials['LINETV']:
            access_token = credentials['LINETV']['access_token']

        if not access_token:
            access_token = self.session.cookies.get_dict().get('accessToken') or ''

        access_token = access_token.strip().strip("'").strip('"')
        if access_token and ' ' not in access_token and access_token.count('.') == 2:
            return f'Bearer {access_token}'
        return access_token

    def _build_api_headers(self, access_token: str = '') -> dict:
        headers = self.session.headers.copy()
        headers.update({
            'referer': 'https://www.linetv.tw/',
            'authority': 'www.linetv.tw'
        })
        if access_token:
            headers['authorization'] = access_token
        return headers

    def _get_legacy_subtitles(self, drama_id: str, episode_index: int, title: str) -> list:
        """Try the older API and public subtitle paths first for free content."""
        member_id = self.session.cookies.get_dict().get('chocomemberId') or ''
        headers = self._build_api_headers()

        try:
            res = self.session.get(
                url=self.config["api"]["manifest"].format(
                    drama_id=drama_id,
                    episode_index=episode_index,
                    app_id=self.config["app_id"],
                    member_id=member_id),
                headers=headers,
                timeout=10)
            if res.ok:
                source = (res.json().get('epsInfo') or {}).get('source') or []
                if source:
                    links = source[0].get('links') or []
                    if links and links[0].get('subtitle'):
                        return [{
                            'url': links[0]['subtitle'],
                            'localeCode': 'zh-Hant'
                        }]
        except Exception:
            pass

        subtitle_link = self.config['api']['sub_1'].format(
            drama_id=drama_id, episode_name=episode_index)
        candidates = [
            subtitle_link,
            subtitle_link.replace(
                'tv-aws-media-convert-input-tokyo',
                'aws-elastic-transcoder-input-tokyo'),
            self.config['api']['sub_2'].format(
                drama_id=drama_id,
                drama_name=quote(title.encode('utf8')),
                episode_name=episode_index)
        ]

        for candidate in candidates:
            if check_url_exist(candidate, headers=headers):
                return [{
                    'url': candidate,
                    'localeCode': 'zh-Hant'
                }]

        return []

    def _get_v2_subtitles(self, drama_id: str, episode_index: int) -> list:
        """Fallback to the newer API for gated content."""
        access_token = self._get_access_token()
        if not access_token:
            return []

        member_id = self.session.cookies.get_dict().get('chocomemberId') or ''
        params = {
            "appId": self.config["app_id"],
            "chocomemberId": member_id
        }
        api_url = f"https://www.linetv.tw/api/part/v2/{drama_id}/eps/{episode_index}/part"

        try:
            res = self.session.get(
                url=api_url,
                params=params,
                headers=self._build_api_headers(access_token),
                timeout=10)
            if res.ok:
                data = res.json()
                return data.get('sourceInfo', {}).get('subtitles') or []
        except Exception:
            pass

        return []

    def get_manifest(self, drama_id: str, episode_index: int, title: str) -> list:
        """Get subtitles with legacy-first fallback."""
        subtitles = self._get_legacy_subtitles(
            drama_id=drama_id, episode_index=episode_index, title=title)
        if subtitles:
            return subtitles

        return self._get_v2_subtitles(
            drama_id=drama_id, episode_index=episode_index)

    def series_metadata(self, data, drama_id):
        title = data['drama_name']
        title, season_index = self.get_title_and_season_index(title)
        self.logger.info(self._("\n%s Season %s"), title, season_index)

        if 'current_eps' in data:
            episode_num = data['current_eps']
            name = rename_filename(f'{title}.S{str(season_index).zfill(2)}')
            folder_path = os.path.join(self.download_path, name)

            if self.last_episode:
                data['eps_info'] = [list(data['eps_info'])[-1]]
                self.logger.info(
                    self._("\nSeason %s total: %s episode(s)\tdownload season %s last episode\n---------------------------------------------------------------"),
                    season_index, episode_num, season_index)
            else:
                self.logger.info(
                    self._("\nSeason %s total: %s episode(s)\tdownload all episodes\n---------------------------------------------------------------"),
                    season_index, episode_num)

            if os.path.exists(folder_path):
                shutil.rmtree(folder_path)

            if 'eps_info' in data:
                subtitles = []
                for episode in data['eps_info']:
                    if 'number' not in episode:
                        continue

                    episode_index = int(episode['number'])

                    if self.download_season and season_index not in self.download_season:
                        continue
                    if self.download_episode and episode_index not in self.download_episode:
                        continue

                    default_name = f'{name}E{str(episode_index).zfill(2)}.WEB-DL.{self.platform}.zh-Hant.vtt'
                    sub_list = self.get_manifest(
                        drama_id=drama_id,
                        episode_index=episode_index,
                        title=title)

                    if sub_list:
                        found_any = False
                        for sub_item in sub_list:
                            sub_url = sub_item.get('url')
                            if not sub_url:
                                continue

                            raw_lang = get_language_code(
                                (sub_item.get('localeCode') or sub_item.get('language') or 'zh-Hant').strip())
                            filename = f'{name}E{str(episode_index).zfill(2)}.WEB-DL.{self.platform}.{raw_lang}.vtt'

                            os.makedirs(folder_path, exist_ok=True)
                            subtitles.append({
                                'name': filename,
                                'path': folder_path,
                                'url': sub_url
                            })
                            found_any = True

                        if not found_any:
                            self.logger.warning(
                                self._("Skipping Episode %s: No valid subtitle URL found."),
                                episode_index)
                    else:
                        if episode.get('free_date'):
                            free_date = strftime(
                                '%Y-%m-%d', localtime(int(episode['free_date']) / 1000))
                            self.logger.info(
                                self._("%s\t...free user will be available on %s"),
                                default_name,
                                free_date)
                        else:
                            self.logger.warning(
                                self._("Skipping Episode %s: No subtitle found."),
                                episode_index)

                self.download_subtitle(subtitles=subtitles, folder_path=folder_path)

    def download_subtitle(self, subtitles, folder_path):
        if subtitles:
            download_files(subtitles)
            convert_subtitle(
                folder_path=folder_path,
                platform=self.platform,
                subtitle_format=self.subtitle_format,
                locale=self.locale)

    def main(self):
        """Download subtitle from LineTV."""
        drama_id = self._get_drama_id(self.url)
        if not drama_id:
            drama_id_search = re.search(r'drama\/(\d+)', self.url)
            if drama_id_search:
                drama_id = drama_id_search.group(1)
            else:
                self.logger.error("\nCan't detect content id: %s", self.url)
                sys.exit(1)

        res = self.session.get(url=self.url, timeout=10)

        if res.ok:
            match = re.search(r'window\.__INITIAL_STATE__ = (\{.*\})', res.text)
            if match:
                try:
                    data = orjson.loads(match.group(1))
                    if 'entities' in data and 'dramaInfo' in data['entities']:
                        drama_info = data['entities']['dramaInfo']['byId'].get(drama_id)
                        if drama_info:
                            self.series_metadata(drama_info, drama_id)
                        else:
                            self.logger.error("\nDrama ID %s not found.", drama_id)
                    else:
                        self.logger.error("\nUnexpected page structure.")
                except Exception as error:
                    self.logger.error("\nError parsing page: %s", error)
            else:
                self.logger.error("\nInitial state not found.")
        else:
            self.logger.error("\nFailed to load page: %s", res.status_code)
