# JLOA 웹사이트

별도 빌드 과정이 없는 정적 웹사이트입니다.

## 파일 구성

- `index.html`: 캐릭터 검색 중심의 웹 메인
- `desktop-app.html`: Windows 데스크톱 앱 소개·다운로드
- `styles.css`, `responsive.css`: 공통 디자인
- `web-app.css`: 웹 메인 전용 디자인
- `app.js`: 공통 내비게이션과 다운로드 설정
- `web-app.js`: 캐릭터 검색과 결과 표시
- `jloa-icon.png`: 브라우저 아이콘
- `downloads/`: 같은 사이트에서 설치 파일을 함께 배포할 때 쓰는 선택 폴더

웹 메인의 캐릭터 검색은 `https://api.jloa.cloud/api/character/{닉네임}`을
호출합니다. 최신 정보 갱신은 같은 주소에 `?fresh=true`를 붙입니다.

## 공개 전 바꿀 값

[app.js](app.js) 맨 위의 다섯 값을 실제 주소로 변경합니다.

```js
const VERSION = "0.0.0";
const DOWNLOAD_URL = "https://download.example.com/JLOA-Setup-0.0.0-x64.exe";
const RELEASE_NOTES_URL = "https://www.example.com/release-notes";
const PRIVACY_URL = "https://www.example.com/privacy";
const AD_POLICY_URL = "https://www.example.com/ad-policy";
```

또한 [index.html](index.html)에 있는 `REPLACE_WITH_SUPPORT_EMAIL`을 실제 지원 이메일로 바꿉니다.

`DOWNLOAD_URL`에 현재 개발 저장소, 로컬 PC 경로, 개인용 클라우드 공유 링크를 넣지 마세요. 공개 배포용으로 분리한 파일 호스팅의 HTTPS 주소를 사용합니다.

## 배포 방식

웹 호스팅 서비스에 `website/` 안의 파일을 그대로 올리면 됩니다. 설치 파일을 같은 도메인에서 배포하려면 `downloads/`에 최종 설치 EXE를 넣고 다음처럼 상대 경로를 쓸 수 있습니다.

```js
const DOWNLOAD_URL = "./downloads/JLOA-Setup-0.0.0-x64.exe";
```

설치 파일이 크거나 다운로드 트래픽이 늘어날 수 있으므로, 공개 출시에서는 웹사이트와 설치 파일 호스팅을 분리하는 방식을 권장합니다.

## 공개 전 확인 목록

- [ ] 설치 파일 URL을 실제로 다운로드할 수 있다.
- [ ] 페이지에 표시되는 버전과 설치 파일 버전이 같다.
- [ ] 릴리스 노트, 개인정보 처리방침, 광고 정책 페이지가 실제로 존재한다.
- [ ] 지원 이메일이 실제 주소다.
- [ ] PC와 모바일에서 메뉴·다운로드 버튼·정책 링크를 확인했다.
- [ ] 다운로드 파일은 코드 서명 후 생성한 최종 SHA-256과 함께 게시했다.
- [ ] 웹페이지와 공개 호스팅 어디에도 개발용 키·토큰·개인 설정을 올리지 않았다.

## 아직 결정이 필요한 것

도메인, 웹 호스팅, 설치 파일 호스팅, 지원 이메일, 개인정보 처리방침 책임 주체, 광고 공급자는 운영자가 결정해야 합니다. 결정 전까지는 이 폴더를 로컬 미리보기와 디자인 검토용으로만 사용합니다.
