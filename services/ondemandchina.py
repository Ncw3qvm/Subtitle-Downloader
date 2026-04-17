#!/usr/bin/python3
# coding: utf-8

"""
This module is to download subtitle from OnDemandChina (ODC)
"""
from __future__ import annotations
import os
import re
import sys
from utils.io import rename_filename, download_files
from utils.helper import get_locale
from utils.subtitle import convert_subtitle
from services.baseservice import BaseService

class ODC(BaseService):
    """
    Service code for the OnDemandChina streaming service (https://www.ondemandchina.com/).
    """

    def __init__(self, args):
        super().__init__(args)
        self._ = get_locale(__name__, self.locale)

        # 从 URL 中提取 slug，例如 love-and-deceit
        slug_match = re.search(r'/(?:program|watch)/([^/?#\s]+)', args.url)
        self.slug = slug_match.group(1) if slug_match else os.path.basename(args.url.rstrip('/'))
        
        # 写入 API 必须的验证 Header (Brightcove Policy Key)
        self.api_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json;pk=BCpkADawqM0qcSQv-zl-EZi5Pf9kJ5MQNmWUWFD8LIkfI9nif6gcgHYIapqS2AqQdxhBQyBxdxTkMEY5BhNySZ7j0WoJDLdm4Wjg_FJReqAZ08vWauM_WmbXLLKhXE-whFKtpNJ2Fxz-ZtCW'
        }
        self.session.headers.update(self.api_headers)

    def movie_metadata(self, data, title, release_year):
        self.logger.info("\n%s (%s)", title, release_year)
        title = rename_filename(f'{title}.{release_year}')

        folder_path = os.path.join(self.download_path, title)
        os.makedirs(folder_path, exist_ok=True)
        
        # 电影通常对应 series/1
        ep_api = f'https://odkmedia.io/odc/api/v1/program/{self.slug}/series/1/'
        ep_res = self.session.get(ep_api, timeout=10)
        
        if not ep_res.ok:
            self.logger.error(self._("\nFailed to get video ID for this movie."))
            sys.exit(0)
            
        video_id = ep_res.json().get('id')
        pb_res = self.session.get(f'https://odkmedia.io/odc/api/v2/playback/{video_id}/', timeout=10)
        tracks = pb_res.json().get('text_tracks', [])

        if not tracks:
            self.logger.warning(self._("\nSorry, there's no embedded subtitles in this video!"))
            sys.exit(0)

        subtitles = []
        languages = set()

        for track in tracks:
            track_url = track.get('url') or track.get('src')
            if not track_url:
                continue
                
            codec = track.get('codec', '').lower()
            
            # 只抓取 SRT 格式 ---
            if 'srt' not in codec and '.srt' not in track_url:
                continue

            lang = track.get('language', 'und')

            subtitle = dict()
            subtitle['name'] = f'{title}.WEB-DL.{self.platform}.{lang}.srt'
            subtitle['path'] = folder_path
            subtitle['url'] = track_url
            subtitles.append(subtitle)
            languages.add(folder_path)

        self.download_subtitle(subtitles=subtitles, languages=languages, folder_path=folder_path)

    def series_metadata(self, data, title, release_year, ep_total):
        title = rename_filename(title)
        self.logger.info(self._("\n%s total: %s episode(s)"), title, ep_total)
        
        # ODC 默认没有严格的 Season 划分，按 S01 处理
        season_index = 1
        name = rename_filename(f'{title}.S{str(season_index).zfill(2)}')
        folder_path = os.path.join(self.download_path, name)
        os.makedirs(folder_path, exist_ok=True)

        # 处理用户输入的集数范围 (--last-episode 或 --episode)
        episodes_to_download = []
        if self.last_episode:
            episodes_to_download = [ep_total]
            self.logger.info(self._("\nDownload season %s last episode\n---------------------------------------------------------------"), season_index)
        elif self.download_episode:
            episodes_to_download = [ep for ep in self.download_episode if ep <= ep_total]
            self.logger.info(self._("\nDownload season %s episodes: %s\n---------------------------------------------------------------"), season_index, episodes_to_download)
        else:
            episodes_to_download = range(1, ep_total + 1)
            self.logger.info(self._("\nDownload all episodes\n---------------------------------------------------------------"))

        subtitles = []
        languages = set()

        for ep_num in episodes_to_download:
            ep_api = f'https://odkmedia.io/odc/api/v1/program/{self.slug}/series/{ep_num}/'
            ep_res = self.session.get(ep_api, timeout=10)
            
            if not ep_res.ok:
                continue
                
            video_id = ep_res.json().get('id')
            if not video_id:
                continue

            pb_res = self.session.get(f'https://odkmedia.io/odc/api/v2/playback/{video_id}/', timeout=10)
            if not pb_res.ok:
                continue

            tracks = pb_res.json().get('text_tracks', [])
            if not tracks:
                self.logger.warning(self._("No subtitles found for episode %s"), ep_num)
                continue

            for track in tracks:
                track_url = track.get('url') or track.get('src')
                if not track_url:
                    continue

                codec = track.get('codec', '').lower()
                
                # --- 核心修改：只抓取 SRT 格式 ---
                if 'srt' not in codec and '.srt' not in track_url:
                    continue

                lang = track.get('language', 'und')

                subtitle = dict()
                subtitle['name'] = f"{name}E{str(ep_num).zfill(2)}.WEB-DL.{self.platform}.{lang}.srt"
                subtitle['path'] = folder_path
                subtitle['url'] = track_url
                subtitles.append(subtitle)
                languages.add(folder_path)

        self.download_subtitle(subtitles=subtitles, languages=languages, folder_path=folder_path)

    def download_subtitle(self, subtitles, languages, folder_path):
        if subtitles and languages:
            download_files(subtitles)
            for lang_path in sorted(languages):
                convert_subtitle(
                    folder_path=lang_path, subtitle_format=self.subtitle_format, locale=self.locale)
            convert_subtitle(folder_path=folder_path,
                             platform=self.platform, subtitle_format=self.subtitle_format, locale=self.locale)

    def main(self):
        # ODC Program API 解析
        api_url = f'https://odkmedia.io/odc/api/v1/program/{self.slug}/'
        res = self.session.get(url=api_url, timeout=10)
        
        if res.ok:
            data = res.json()
            title = data.get('title_zh_Hans') or data.get('title_en') or self.slug
            release_year = str(data.get('year', ''))
            ep_total = data.get('meta', {}).get('episode_total', 1)

            # ODC 判断是电影还是剧集 (基于总集数)
            if ep_total <= 1:
                self.movie_metadata(data, title, release_year)
            else:
                self.series_metadata(data, title, release_year, ep_total)
        else:
            self.logger.error(self._("Failed to fetch program metadata from ODC API."))