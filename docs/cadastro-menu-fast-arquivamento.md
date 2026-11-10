# Cadastro, navegação e cobranças Fast

- O cadastro completo está em `/cadastro`; a página inicial oferece o botão **FAZER CADASTRO AGORA**. Formulário, assinatura, confirmação por e-mail e validações foram reaproveitados.
- Configurações reúne Pacotes e retiradas, Histórico e Gerenciar blog, conforme as permissões existentes. O menu superior do ponto contém métricas (quando autorizado), Configurações e Sair. As seis abas do cliente ficam visíveis e quebram em linhas no celular.
- Os textos visíveis usam Fast. Nomes técnicos, ambiente, APIs, URLs e webhooks continuam `fastflow`/`FASTFLOW`.
- Cancelamento: consulta por ID, valida valor e vínculo, envia `DELETE /transactions/{id}` somente se não pago e consulta novamente. HTTP 204 sozinho não confirma o cancelamento. Falhas conservam o status financeiro e impedem nova cobrança enquanto houver incerteza.
- Arquivamento: disponível no detalhe administrativo da cobrança Pix Fast. Não cancela a transação: oculta a lista principal e o QR Code do cliente, mantém auditoria, reservas e consultas agendadas. O histórico arquivado está na tela do plano ou em **Configurações → Cobranças de pacotes → Histórico de cobranças arquivadas**.
- Pagamento posterior ao arquivamento fica registrado como pago e sinalizado para conferência, sem ativar ciclo ou quitar pacotes automaticamente. Não é criado outro Pix para o mesmo pedido ainda ativo/incerto.

## Verificação

Testes automatizados cobrem cadastro/menu/permissões, preços existentes, cancelamento confirmado, falha, cobrança paga, arquivamento, repetição, pagamento posterior, reconciliação sem webhook e persistência após reinicialização. Usam API simulada, sem cobranças ou e-mails reais. `tests/browser/eleven_updates.py` verifica 320, 360, 390, 430, 768, 1024 e 1440 px, cadastro, mapa, seis abas, menu, gráficos e arquivamento. O gráfico pode rolar internamente quando há muitos períodos, mantendo a página sem rolagem lateral.

## Oracle (após merge)

Sem novas credenciais, variáveis, dependências ou alterações nos agendamentos. Faça o backup usual do banco e confira o serviço/caminho efetivo antes de atualizar:

```bash
sudo systemctl show ponto-coleta.service -p WorkingDirectory -p ExecStart -p EnvironmentFiles
# Na pasta WorkingDirectory, com main limpa:
git switch main
git pull --ff-only origin main
sudo systemctl restart ponto-coleta.service
sudo systemctl status ponto-coleta.service --no-pager
```

Se a instalação usar `me-guarde.service`, utilize esse nome. A inicialização acrescenta apenas colunas de arquivamento e controle de cancelamento; não apaga registros. Confirme os jobs existentes e faça uma conferência visual de `/cadastro` e dos históricos. A implantação Oracle não foi executada no ambiente de desenvolvimento.
