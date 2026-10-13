from core.security import new_token
print('TALOS_LAB_ADMIN_TOKEN='+new_token())
print('TALOS_LAB_WORKER_TOKEN='+new_token())
print('Store them privately. They are printed once and never bundled into ZIP/GitHub.')
