# -*- coding: UTF-8 -*-

'''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''
'                                                                         '
' Copyright 2018 Gauthier Brière (gauthier.briere "at" gmail.com)         '
'                                                                         '
' This file is part of cn5X                                               '
'                                                                         '
' cn5X is free software: you can redistribute it and/or modify it         '
'  under the terms of the GNU General Public License as published by      '
' the Free Software Foundation, either version 3 of the License, or       '
' (at your option) any later version.                                     '
'                                                                         '
' cn5X is distributed in the hope that it will be useful, but             '
' WITHOUT ANY WARRANTY; without even the implied warranty of              '
' MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the           '
' GNU General Public License for more details.                            '
'                                                                         '
' You should have received a copy of the GNU General Public License       '
' along with this program.  If not, see <http://www.gnu.org/licenses/>.   '
'                                                                         '
'''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''''

import sys, time
from math import *
from PyQt5.QtCore import QCoreApplication, QObject, QThread, QTimer, QEventLoop, pyqtSignal, pyqtSlot, QIODevice
from PyQt5.QtSerialPort import QSerialPort, QSerialPortInfo
from grblCommunicator_stack import grblSerialStack

class serialCommunicator(QObject):
  """
  Worker must derive from QObject in order to emit signals,
  connect slots to other signals, and operate in a QThread.
  """
  sig_msg        = pyqtSignal(str) # Messages de fonctionnements
  sig_init       = pyqtSignal(str) # Emis à la réception de la chaine d'initialisation de Grbl
  sig_grbl_ok    = pyqtSignal()    # Emis quand Grbl à fini son initialisation
  sig_ok         = pyqtSignal()    # Reponses "ok"
  sig_response   = pyqtSignal(str) # Reponses "error:X" ou "ALARM:X"
  sig_error      = pyqtSignal()    # Error trap
  sig_alarm      = pyqtSignal()    # Alarm trap
  sig_status     = pyqtSignal(str) # Status "<***>"
  sig_data       = pyqtSignal(str) # Emis à chaque autre ligne de données reçue
  sig_data_debug = pyqtSignal(str) # All data from Grbl
  sig_send_ok    = pyqtSignal()    # Emis à chaque ligne envoyée
  sig_done       = pyqtSignal()    # Emis à la fin du thread

  def __init__(self, comPort: str, baudRate: int, stack: grblSerialStack):
    super().__init__()
    self.__abort          = False
    self.__portName       = comPort
    self.__baudRate       = baudRate
    self.__okToSend       = False
    self.__grblStatus     = ""
    self.__okTrap         = 0
    self.__serialStack    = stack
    self.__tDernierEnvoi  = 0
    self.__timeOutEnQueue = 60 # Timeout sur l'envoi d'une nouvelle ligne si Grbl n'a pas répondu OK en 1 minute.

  @pyqtSlot()
  def run(self):
    thread_name = QThread.currentThread().objectName()
    thread_id = int(QThread.currentThreadId())  # cast to int() is necessary
    self.sig_msg.emit('Running "{}" from thread #{}.'.format(thread_name, hex(thread_id)))

    # Configuration du port série
    self.__comPort = QSerialPort()
    self.__comPort.setPortName(self.__portName)
    self.__comPort.setBaudRate(self.__baudRate)
    self.__comPort.setDataBits(QSerialPort.Data8)
    self.__comPort.setStopBits(QSerialPort.OneStop)
    self.__comPort.setParity(QSerialPort.NoParity)

    # Ouverture du port série
    RC = False
    try:
      RC = self.__comPort.open(QIODevice.ReadWrite)
    except OSError as err:
      self.sig_msg.emit("serialCommunicator : Erreur ouverture du port : {0}".format(err))
    except:
      self.sig_msg.emit("serialCommunicator : Unexpected error : {}".format(sys.exc_info()[0]))

    if RC:
      self.sig_msg.emit("serialCommunicator : Ouverture comPort {} : RC = {}".format(self.__comPort.portName(), RC))
    else:
      self.sig_msg.emit("serialCommunicator : Erreur à l'ouverture du port série : err# = {0}".format(self.__comPort.error()))


    # Boucle de lecture du port série
    s = ''
    while 1:
      if self.__comPort.waitForReadyRead(20):
        buff = self.__comPort.readAll()
        try:
          s += buff.data().decode()
          # Découpe les données reçues en lignes pour les envoyer une par une
          tblLines = s.splitlines()
        except:
          self.sig_msg.emit("serialCommunicator : Erreur décodage : {}".format(sys.exc_info()[0]))
          s = ''
        if s != '':
          if s[-1] == "\n":
            # La dernière ligne est complette, on envoi tout
            for l in tblLines:
              self.traileLaLigne(l)
            s=''
          else:
            # La dernière ligne est incomplette, on envoi jusqu'à l'avant dernière.
            for l in tblLines[:-1]:
              self.traileLaLigne(l)
            # On laisse la derniere ligne dans le buffer pour qu'elle soit complettée.
            s = tblLines[-1]

      # Process events to receive signals;
      QCoreApplication.processEvents()
      if self.__abort:
        self.sig_msg.emit("serialCommunicator : aborting...")
        break

      # ICI, Traiter le vidage de la pile
      if self.__serialStack.count() > 0:
        self.deQueue()

    # Sortie de la boucle de lecture
    self.sig_msg.emit("serialCommunicator : Fermeture du port série.")
    self.__comPort.close()
    # Emission du signal de fin
    self.sig_done.emit()

  def traileLaLigne(self, l):
    # Envoi de toutes les lignes dans le debug
    self.sig_data_debug.emit("<<< " + l)
    # Grbl 1.1f ['$' for help] (Init string)
    if l[:5] == "Grbl " and l[-5:] == "help]":
      self.__okToSend = True
      self.__grblStatus = "Idle"
      self.sig_msg.emit("serialCommunicator : Grbl prêt pour recevoir des données")
      self.sig_init.emit(l)
      self.sig_grbl_ok.emit()

    elif l[:1] == "<" and l[-1:] == ">": # Real-time Status Reports
      if self.__grblStatus != l[1:-1].split("|")[0]:
        self.__grblStatus = l[1:-1].split("|")[0]
      self.sig_status.emit(l)
    elif l == "ok": # Reponses "ok"
      self.__okToSend = True
      if self.__okTrap > 0:
        self.__okTrap -= 1
      else:
        self.sig_ok.emit()

    elif l[:6] == "error:": # "error:X"
      self.sig_response.emit(l)
      self.sig_error.emit()
      self.__okToSend = True # la reception d'un message d'erreur fait l'acquitement de la commande qui à provoqué l'erreur.
    elif l[:6] == "ALARM:": # "ALARM:X"
      self.sig_response.emit(l)
      self.sig_alarm.emit()
    else:
      self.sig_data.emit(l)

  @pyqtSlot()
  def isOkToSend(self):
    return self.__okToSend

  @pyqtSlot()
  def grblStatus(self):
    return self.__grblStatus

  @pyqtSlot()
  def deQueue(self):
    # On récupère le prochain élément de la queue
    buff = self.__serialStack.deQueue()
    if buff == None:
      elf.sig_msg.emit("grblCommunicator: deQueue() la file d'attente est vide !")
      return

    # On vérifie si Grbl a traité les éléments précédents
    if not self.__okToSend:
      # l'élément précédent n'est pas encore traité
      if time.time() > self.__tDernierEnvoi + self.__timeOutEnQueue:
        self.sig_msg.emit("grblCommunicator: deQueue({}) timeout ! Utilisez ^X (Ctrl + X) pour réinitialiser Grbl".format(buff))
        self.__serialStack.clear()
        return
    else:
      # C'est bon, on envoie
      self.__sendLine(buff, False)
      self.__tDernierEnvoi = time.time()

  @pyqtSlot(str, bool)
  def __sendLine(self, buff: str, trapOk: bool = False):
    '''
    Ne doit jamais être appelé directement, sauf par deQueue() ou par les timers
    '''
    # Force la fin de ligne et envoie
    if buff[-1:] != '\n':
      self.sendData(buff + '\n', trapOk)
    else:
      self.sendData(buff, trapOk)

  @pyqtSlot(str, bool)
  def sendData(self, buff: str, trapOk: bool = False):

    # Envoi de toutes les lignes dans le debug
    if buff[-1:] == "\n":
      self.sig_data_debug.emit("<<< " + buff[:-1] + "\\n")
    elif buff[-2:] == "\r\n":
      self.sig_data_debug.emit("<<< " + buff[:-2] + "\\r\\n")
    else:
      self.sig_data_debug.emit("<<< " + buff)

    if buff not in [chr(0x18), chr(0xA0), chr(0xA1), chr(0x85)]:
      # Les commandes "real-time" n'on pas besoin d'attendre okToSend...
      if not self.__okToSend:
        self.sig_msg.emit("serialCommunicator : Erreur : Grbl pas prêt pour recevoir des données")
        return

    if trapOk:
      self.__okTrap = 1 # La prochaine réponse "ok" de Grbl ne sera pas transmise

    if buff == chr(0x85):
      print("sendData(), envoi du Jog cancel...")

    # Formatage du buffer à envoyer
    buffWrite = bytes(buff, sys.getdefaultencoding())
    tempNecessaire = ceil(1000 * len(buffWrite) * 8 / self.__baudRate) # Temps nécessaire pour la com (millisecondes), arrondi à l'entier supérieur
    timeout = 10 + (2 * tempNecessaire) # 2 fois le temps nécessaire + 10 millisecondes
    self.__comPort.write(buffWrite)
    if self.__comPort.waitForBytesWritten(timeout):
      self.sig_send_ok.emit()
    else:
      self.sig_msg.emit("serialCommunicator : Erreur envoi des données : timeout")

    if buff[-1:] == '\n':
      # On doit recevoir 1 ok à chaque ligne envoyée, sauf pour les commandes temps réel qui n'ont pas de retour chariot
      self.__okToSend = False

  @pyqtSlot()
  def abort(self):
    self.sig_msg.emit("serialCommunicator : abort reçu.")
    self.__abort = True