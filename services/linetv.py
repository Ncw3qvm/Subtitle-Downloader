#!/usr/bin/python3
# coding: utf-8

"""
This module is to download subtitle from LineTV
[Restored & Fixed]: Class name 'LineTV', V2 API, Config Support
"""

import os
import re
import shutil
import sys
import orjson
from urllib.parse import urlparse
from configs.config import credentials
from utils.io import rename_filename, download_files
from utils.helper import get_locale
from utils.subtitle import convert_subtitle
from services.baseservice import BaseService

class LineTV(BaseService):
    """
    Service code for Line TV streaming service (https://www.linetv.tw/).
    """

    def __init__(self, args):
        super().__init__(args)
        self._ = get_locale(__name__, self.locale)

    def _get_drama_id(self, url):
        """Robustly extract Drama ID from URL path"""
        try:
            path = urlparse(url).path
            match = re.search(r'^/drama/(\d+)(?=/|$)', path)
            if match:
                return match.group(1)
        except Exception:
            pass
        return None

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
                self.logger.info(self._("\nSeason %s total: %s episode(s)\tdownload season %s last episode\n---------------------------------------------------------------"),
                                 season_index, episode_num, season_index)
            else:
                self.logger.info(self._("\nSeason %s total: %s episode(s)\tdownload all episodes\n---------------------------------------------------------------"),
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
                                
                    sub_list = self.get_manifest(drama_id=drama_id, episode_index=episode_index)
                    
                    if sub_list:
                        found_any = False
                        for sub_item in sub_list:
                            sub_url = sub_item.get('url')
                            if not sub_url: continue
                            
                            raw_lang = sub_item.get('localeCode', '').strip() or 'unk'
                            filename = f'{name}E{str(episode_index).zfill(2)}.WEB-DL.{self.platform}.{raw_lang}.vtt'
                            
                            os.makedirs(folder_path, exist_ok=True)
                            subtitle = {'name': filename, 'path': folder_path, 'url': sub_url}
                            subtitles.append(subtitle)
                            found_any = True
                        
                        if not found_any:
                                self.logger.warning(self._("Skipping Episode %s: No valid subtitle URL found."), episode_index)
                    else:
                        self.logger.warning(self._("Skipping Episode %s: No subtitle found."), episode_index)

                self.download_subtitle(subtitles=subtitles, folder_path=folder_path)

    def get_manifest(self, drama_id: str, episode_index: int) -> list:
        """Get manifest V2"""
        member_id = self.session.cookies.get_dict().get('chocomemberId') or ''
        
        # 🟢 Token 读取逻辑（兼容 LineTV 和 LINETV 写法）
        access_token = ''
        
        # 1. 尝试从 self.platform (通常是 'LineTV') 读取
        if self.platform in credentials and 'access_token' in credentials[self.platform]:
            access_token = credentials[self.platform]['access_token']
        
        # 2. 如果没读到，尝试强制读取 'LINETV' (以防配置文件写的是全大写)
        if not access_token and 'LINETV' in credentials and 'access_token' in credentials['LINETV']:
             access_token = credentials['LINETV']['access_token']

        # 3. 都没有就读 Cookie
        if not access_token:
            access_token = self.session.cookies.get_dict().get('accessToken') or ''
        
        if access_token:
            clean_token = access_token.strip().strip("'").strip('"')
            self.session.headers.update({'authorization': clean_token})

        self.session.headers.update({
            'referer': 'https://www.linetv.tw/',
            'authority': 'www.linetv.tw'
        })

        app_id = "062097f1b1f34e11e7f82aag22000aee"
        api_url = f"https://www.linetv.tw/api/part/v2/{drama_id}/eps/{episode_index}/part"
        params = {"appId": app_id, "chocomemberId": member_id}

        try:
            res = self.session.get(url=api_url, params=params, timeout=10)
            if res.ok:
                data = res.json()
                return data.get('sourceInfo', {}).get('subtitles')
        except Exception:
            pass
        return None

    def download_subtitle(self, subtitles, folder_path):
        if subtitles:
            download_files(subtitles)
            convert_subtitle(folder_path=folder_path,
                             platform=self.platform, subtitle_format=self.subtitle_format, locale=self.locale)

    def main(self):
        """Download subtitle from LineTV"""
        drama_id = self._get_drama_id(self.url)
        if not drama_id:
            # Fallback for old regex
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
                            self.logger.error(f"\nDrama ID {drama_id} not found.")
                    else:
                         self.logger.error("\nUnexpected page structure.")
                except Exception as e:
                    self.logger.error(f"\nError parsing page: {e}")
            else:
                self.logger.error("\nInitial state not found.")
        else:
            self.logger.error(f"\nFailed to load page: {res.status_code}")
