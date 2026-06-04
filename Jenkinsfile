pipeline {
  agent any

  options {
    timestamps()
  }

  stages {
    stage('Checkout') {
      steps {
        git branch: 'main', url: 'git@10.1.0.16:rtjipjes/pokering-points-app.git'
      }
    }

    stage('Python lint') {
      steps {
        sh label: 'Create Python virtualenv', script: '''#!/bin/sh
          set -eu
          python3 -m venv .venv
        '''
        sh label: 'Install Python dependencies', script: '''#!/bin/sh
          set -eu
          . .venv/bin/activate
          pip install --upgrade pip
          pip install -r requirements.txt -r requirements-dev.txt
        '''
        sh label: 'Black check', script: '''#!/bin/sh
          set -eu
          . .venv/bin/activate
          black --check .
        '''
        sh label: 'Ruff check', script: '''#!/bin/sh
          set -eu
          . .venv/bin/activate
          ruff check .
        '''
      }
    }

    stage('Frontend lint') {
      steps {
        sh label: 'Install frontend dependencies', script: '''#!/bin/sh
          set -eu
          if [ -f package-lock.json ]; then
            npm ci --no-audit --no-fund
          else
            npm install --no-audit --no-fund
          fi
        '''
        sh label: 'npm lint', script: '''#!/bin/sh
          set -eu
          npm run lint
        '''
        sh label: 'npm format', script: '''#!/bin/sh
          set -eu
          npm run format
        '''
      }
    }

    stage('Dependency audit') {
      steps {
        sh label: 'Python dependency audit', script: '''#!/bin/sh
          set -eu
          . .venv/bin/activate
          pip-audit -r requirements.txt
        '''
        sh label: 'npm dependency audit', script: '''#!/bin/sh
          set -eu
          npm run audit:deps
        '''
      }
    }

    stage('Deploy') {
      steps {
        sh label: 'Deploy with Ansible', script: '''#!/bin/sh
          set -eu
          ssh -o BatchMode=yes ansible@10.1.0.17 \
            'cd /opt/infra-ansible && ansible-playbook playbooks/pokering-deploy.yml'
        '''
      }
    }
  }
}
