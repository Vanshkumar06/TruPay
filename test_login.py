#!/usr/bin/env python
import requests
from bs4 import BeautifulSoup

def main():
    ua = 'Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36'

    session = requests.Session()
    response = session.get('http://localhost:8000/login/', headers={'User-Agent': ua})
    print(f'GET /login/ status: {response.status_code}')
    print(f'User-Agent used: {ua}')
    print('Form exists:', '<form' in response.text and '</form>' in response.text)
    print('CSRF present:', 'csrfmiddlewaretoken' in response.text)

    soup = BeautifulSoup(response.text, 'html.parser')
    csrf_input = soup.find('input', {'name': 'csrfmiddlewaretoken'})
    csrf_token = csrf_input['value'] if csrf_input else None
    print(f'CSRF Token found: {csrf_token is not None}')
    print(f'CSRF Token sample: {csrf_token[:20]}...' if csrf_token else 'No CSRF token found')

    if csrf_token:
        login_data = {
            'username': 'alice',
            'password': 'Hackathon@123',
            'csrfmiddlewaretoken': csrf_token
        }
        post_response = session.post('http://localhost:8000/login/', data=login_data, headers={'User-Agent': ua}, allow_redirects=True)
        print(f'POST /login/ status: {post_response.status_code}')
        print(f'Redirected to: {post_response.url}')
        print(f'Login successful: {"/app/" in post_response.url}')
    else:
        print('Could not find CSRF token, form will not submit')


if __name__ == '__main__':
    main()
