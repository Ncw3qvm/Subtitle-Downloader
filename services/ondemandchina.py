#!/usr/bin/python3
# coding: utf-8

"""
This module is to download subtitle from OnDemandChina (ODC).
"""

from __future__ import annotations

import os
import re
import sys

from services.baseservice import BaseService
from utils.helper import get_locale
from utils.io import download_files, rename_filename
from utils.subtitle import convert_subtitle


class ODC(BaseService):
    """
    Service code for the OnDemandChina streaming service.
    """

    def __init__(self, args):
        super().__init__(args)
        self._ = get_locale(__name__, self.locale)

        slug_match = re.search(r'/(?:program|watch)/([^/?#\s]+)', args.url)
        self.slug = slug_match.group(1) if slug_match else os.path.basename(args.url.rstrip('/'))

        self.api_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json;pk=BCpkADawqM0qcSQv-zl-EZi5Pf9kJ5MQNmWUWFD8LIkfI9nif6gcgHYIapqS2AqQdxhBQyBxdxTkMEY5BhNySZ7j0WoJDLdm4Wjg_FJReqAZ08vWauM_WmbXLLKhXE-whFKtpNJ2Fxz-ZtCW'
        }
        self.session.headers.update(self.api_headers)

    def get_episode_video_id(self, episode_number: int):
        episode_api = f'https://odkmedia.io/odc/api/v1/program/{self.slug}/series/{episode_number}/'
        episode_response = self.session.get(episode_api, timeout=10)
        if not episode_response.ok:
            return None
        return episode_response.json().get('id')

    def get_text_tracks(self, video_id: str) -> list:
        playback_response = self.session.get(
            f'https://odkmedia.io/odc/api/v2/playback/{video_id}/', timeout=10)
        if not playback_response.ok:
            return []
        return playback_response.json().get('text_tracks', [])

    def collect_preferred_subtitles(self, tracks, name_prefix, folder_path):
        subtitles_by_type = {
            '.srt': [],
            '.vtt': [],
            '.ass': []
        }

        for track in tracks:
            track_url = track.get('url') or track.get('src')
            if not track_url:
                continue

            codec = (track.get('codec') or '').lower()
            track_url_lower = track_url.lower()

            if 'srt' in codec or '.srt' in track_url_lower:
                extension = '.srt'
            elif 'vtt' in codec or '.vtt' in track_url_lower:
                extension = '.vtt'
            elif 'ass' in codec or '.ass' in track_url_lower:
                extension = '.ass'
            else:
                continue

            language = track.get('language', 'und')
            subtitles_by_type[extension].append({
                'name': f'{name_prefix}.{language}{extension}',
                'path': folder_path,
                'url': track_url
            })

        return (
            subtitles_by_type['.srt']
            or subtitles_by_type['.vtt']
            or subtitles_by_type['.ass']
        )

    def movie_metadata(self, title, release_year):
        self.logger.info("\n%s (%s)", title, release_year)
        title = rename_filename(f'{title}.{release_year}')

        folder_path = os.path.join(self.download_path, title)
        os.makedirs(folder_path, exist_ok=True)

        video_id = self.get_episode_video_id(1)
        if not video_id:
            self.logger.error(self._("\nFailed to get video ID for this movie."))
            sys.exit(0)

        tracks = self.get_text_tracks(video_id)
        if not tracks:
            self.logger.warning(self._("\nSorry, there's no embedded subtitles in this video!"))
            sys.exit(0)

        subtitles = self.collect_preferred_subtitles(
            tracks=tracks,
            name_prefix=f'{title}.WEB-DL.{self.platform}',
            folder_path=folder_path)
        languages = {folder_path} if subtitles else set()

        self.download_subtitle(subtitles=subtitles, languages=languages, folder_path=folder_path)

    def series_metadata(self, title, ep_total):
        title = rename_filename(title)
        self.logger.info(self._("\n%s total: %s episode(s)"), title, ep_total)

        season_index = 1
        name = rename_filename(f'{title}.S{str(season_index).zfill(2)}')
        folder_path = os.path.join(self.download_path, name)
        os.makedirs(folder_path, exist_ok=True)

        if self.last_episode:
            episodes_to_download = [ep_total]
            self.logger.info(
                self._("\nDownload season %s last episode\n---------------------------------------------------------------"),
                season_index)
        elif self.download_episode:
            episodes_to_download = [ep for ep in self.download_episode if ep <= ep_total]
            self.logger.info(
                self._("\nDownload season %s episodes: %s\n---------------------------------------------------------------"),
                season_index, episodes_to_download)
        else:
            episodes_to_download = range(1, ep_total + 1)
            self.logger.info(self._("\nDownload all episodes\n---------------------------------------------------------------"))

        subtitles = []
        languages = set()

        for ep_num in episodes_to_download:
            video_id = self.get_episode_video_id(ep_num)
            if not video_id:
                continue

            tracks = self.get_text_tracks(video_id)
            if not tracks:
                self.logger.warning(self._("No subtitles found for episode %s"), ep_num)
                continue

            episode_subtitles = self.collect_preferred_subtitles(
                tracks=tracks,
                name_prefix=f"{name}E{str(ep_num).zfill(2)}.WEB-DL.{self.platform}",
                folder_path=folder_path)
            if not episode_subtitles:
                self.logger.warning(self._("No supported subtitles found for episode %s"), ep_num)
                continue

            subtitles.extend(episode_subtitles)
            languages.add(folder_path)

        self.download_subtitle(subtitles=subtitles, languages=languages, folder_path=folder_path)

    def download_subtitle(self, subtitles, languages, folder_path):
        if subtitles and languages:
            download_files(subtitles)
            for lang_path in sorted(languages):
                convert_subtitle(
                    folder_path=lang_path,
                    subtitle_format=self.subtitle_format,
                    locale=self.locale)
            convert_subtitle(
                folder_path=folder_path,
                platform=self.platform,
                subtitle_format=self.subtitle_format,
                locale=self.locale)

    def main(self):
        api_url = f'https://odkmedia.io/odc/api/v1/program/{self.slug}/'
        response = self.session.get(url=api_url, timeout=10)

        if response.ok:
            data = response.json()
            title = data.get('title_zh_Hans') or data.get('title_en') or self.slug
            release_year = str(data.get('year', ''))
            ep_total = data.get('meta', {}).get('episode_total', 1)

            if ep_total <= 1:
                self.movie_metadata(title, release_year)
            else:
                self.series_metadata(title, ep_total)
        else:
            self.logger.error(self._("Failed to fetch program metadata from ODC API."))
